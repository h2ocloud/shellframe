#!/usr/bin/env python3
"""The desktop's chat view, backend side (api_chat.py; the screen is web/chat.js).

  - settings.ui_style: a first launch (no config.json) opens on "chat"; a config.json
    written before the setting existed is an existing computer and gets "cli", once,
    on disk; a chosen value stays
  - chat_view gives the phone's data and nothing else: the turns are what sfctl
    `conversation` (GET /link/conversation) gives, the question is `_link_decision`'s
    (the `decision` of GET /link/bots); a CLI the view does not draw, or a tab with no
    record, says why; `rev` answers "unchanged" until the record grows
  - chat_send is the phone's message box (`send` → tab_delivery.deliver: queued when busy)
  - chat_answer is /link/answer: only while that very question is up, only a listed
    option, the option's own number and Enter; Telegram's copies say "on the computer"
    (the phone's answer still says "on the phone")
  - every js_api method web/chat.js calls exists on the backend

Run: .venv/bin/python tests_chat_shell.py
"""
import json
import os
import re
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-chat-shell-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "shellframe_bridge.log")

import api_chat  # noqa: E402
import api_remote  # noqa: E402
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
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def on_disk():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


print("settings.ui_style: new and existing computers")
check("the default is chat", main.DEFAULT_CONFIG["settings"]["ui_style"] == "chat")
main.CONFIG_FILE.unlink(missing_ok=True)
first = main.load_config()
check("a first launch (no config.json) opens on chat", first["settings"]["ui_style"] == "chat", first["settings"])
check("…and reading it writes nothing", not main.CONFIG_FILE.exists())
main.save_config(first)
check("…its first save keeps chat", main.load_config()["settings"]["ui_style"] == "chat" and on_disk()["settings"]["ui_style"] == "chat")

main.CONFIG_FILE.write_text(json.dumps({"settings": {"fontSize": 13, "language": "zh-TW"}, "presets": []}), encoding="utf-8")
old = main.load_config()
check("a config.json from before the setting is an existing computer: cli", old["settings"]["ui_style"] == "cli", old["settings"])
check("…written to disk once, the rest of its settings untouched",
      on_disk()["settings"] == {"fontSize": 13, "language": "zh-TW", "ui_style": "cli"}, on_disk()["settings"])
check("…and it stays cli", main.load_config()["settings"]["ui_style"] == "cli")
for chosen in ("chat", "cli"):
    main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": chosen}}), encoding="utf-8")
    check(f"a chosen {chosen} stays {chosen}", main.load_config()["settings"]["ui_style"] == chosen)
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "terminal"}}), encoding="utf-8")
check("an unknown value is read as an existing computer's cli", main.load_config()["settings"]["ui_style"] == "cli")
main.CONFIG_FILE.write_text(json.dumps({"presets": []}), encoding="utf-8")
check("a config.json with no settings at all: cli", main.load_config()["settings"]["ui_style"] == "cli")

api = Api.__new__(Api)
api.sessions = {}
res = json.loads(api.save_settings(json.dumps({"ui_style": "chat", "fontSize": 13})))
check("the page saves it like any other setting", res["settings"]["ui_style"] == "chat" and on_disk()["settings"]["ui_style"] == "chat")
main.CONFIG_FILE.write_text(json.dumps({"settings": {"ui_style": "chat"}}), encoding="utf-8")


# ── fake tabs over real transcript files ──────────────────────────────────────
class Tab:
    def __init__(self, sid, cmd, label=None):
        self.sid, self.cmd, self._custom_label, self.alive = sid, cmd, label, True
        self.cwd, self._tmux_name, self.lock = "~", None, None
        self.written = []

    def write(self, data):
        self.written.append(data)


def rec(kind, content, ts, **extra):
    return json.dumps({"type": kind, "sessionId": "demo", "timestamp": ts,
                       "message": {"role": kind, "content": content, **extra}}, ensure_ascii=False)


CLAUDE = TMP / "claude.jsonl"
CLAUDE.write_text("\n".join([
    rec("user", "整理一下週報", "2026-11-10T10:00:00Z"),
    rec("assistant", [{"type": "tool_use", "name": "Read", "input": {"file_path": "/work/notes.md"}},
                      {"type": "tool_use", "name": "Bash", "input": {"command": "npm test"}}], "2026-11-10T10:00:05Z"),
    rec("user", [{"type": "tool_result", "content": "ok"}], "2026-11-10T10:00:09Z"),
    rec("assistant", [{"type": "text", "text": "好了，週報在 `reports/weekly.md`"}], "2026-11-10T10:00:12Z",
        stop_reason="end_turn"),
]) + "\n", encoding="utf-8")
CODEX = TMP / "codex.jsonl"
CODEX.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in [
    {"type": "session_meta", "timestamp": "2026-11-10T10:00:00Z", "payload": {"id": "x"}},
    {"type": "event_msg", "timestamp": "2026-11-10T10:00:01Z", "payload": {"type": "user_message", "message": "跑一下測試"}},
    {"type": "response_item", "timestamp": "2026-11-10T10:00:02Z",
     "payload": {"type": "function_call", "name": "exec_command", "arguments": json.dumps({"cmd": "pytest -q"})}},
    {"type": "event_msg", "timestamp": "2026-11-10T10:00:08Z", "payload": {"type": "agent_message", "message": "全部通過"}},
]) + "\n", encoding="utf-8")
PATHS = {"s1": CLAUDE, "s2": CODEX}
STATUS = {"s1": {"state": "done"}, "s2": {"state": "working", "action": "Running pytest -q"},
          "s3": {"state": "decision"}, "s4": {"state": "idle"}, "s5": {"state": "idle"}}
SCREENS = {"s3": "\n".join(["⏺ Bash(touch notes.txt)", "  ⎿ \xa0Waiting…", "", "─" * 60, " Bash command",
                            " Create empty file notes.txt", "╌" * 60, " touch notes.txt", "╌" * 60,
                            " Do you want to proceed?", " ❯ 1. Yes",
                            "   2. Yes, and always allow access to /repo from this project", "   3. No", "",
                            " Esc to cancel · Tab to amend"])}
PATHS["s3"] = CLAUDE

api.sessions = {"s1": Tab("s1", "claude --model haiku", "週報"), "s2": Tab("s2", "codex --full-auto"),
                "s3": Tab("s3", "claude"), "s4": Tab("s4", "bash"), "s5": Tab("s5", "claude")}
api.bridge = api.line_bridge = None
api._window = None
api._agent_status_snapshot = lambda sid: (dict(STATUS.get(sid, {}), prompt_at=0.0), 0)
api._agent_state_for_list = lambda sid: STATUS.get(sid, {}).get("state", "")
api._worker_ctx = lambda sid, s: {"sid": sid, "cmd": s.cmd, "config_dir": ""}
main.agent_status.resolve_transcript = lambda worker: str(PATHS[worker["sid"]]) if worker.get("sid") in PATHS else None
tab_delivery.screen_text = lambda api_, sid, lines=60: SCREENS.get(sid, "")


def view(sid, rev=""):
    return json.loads(api.chat_view(sid, 120, rev))


print("chat_view: the phone's data")
v1 = view("s1")
phone = api._execute_sfctl("conversation", {"sid": "s1", "limit": 120})
check("the turns are exactly what GET /link/conversation gives", v1["turns"] == phone["details"]["turns"]
      and v1["turns"] == api_remote.conversation_turns(str(CLAUDE), 120)[1], v1["turns"])
check("…a Claude tab: readable, its state, no question", v1["readable"] and v1["provider"] == "claude"
      and v1["activity"] == "done" and v1["decision"] is None, {k: v1[k] for k in ("readable", "provider", "activity", "decision")})
check("…tool calls arrive as calls (the view groups them, nothing is dropped)",
      [t["tool"] for t in v1["turns"] if t["kind"] == "tool_call"] == ["Read", "Bash"])
v2 = view("s2")
check("a Codex tab too", v2["readable"] and v2["provider"] == "codex" and v2["activity"] == "working"
      and [t["kind"] for t in v2["turns"]] == ["user_msg", "tool_call", "assistant_text"], v2)
v4 = view("s4")
check("a bash tab: not readable, because of what it runs", not v4["readable"] and v4["why"] == "provider"
      and v4["turns"] == [] and v4["provider"] == "other", v4)
v5 = view("s5")
check("a Claude tab with no record yet: says so", not v5["readable"] and v5["why"] == "no_transcript", v5)
check("no such tab", json.loads(api.chat_view("s9", 120, "")) == {"success": False, "code": "no_tab"})

print("chat_view: the question")
v3 = view("s3")
cur = api._link_decision("s3", "decision", {"state": "decision"})
check("a menu on screen: the very decision /link/bots shows", v3["decision"] == cur and cur is not None, v3["decision"])
check("…its options with their own numbers", [(o["n"], o["text"]) for o in v3["decision"]["options"]]
      == [(1, "Yes"), (2, "Yes, and always allow access to /repo from this project"), (3, "No")], v3["decision"])
RED = [{"kind": "user_msg", "text": "修哪個"},
       {"kind": "assistant_text", "text": "兩個都可以。\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知"}]
bridge_home.on_turn_end(api, "s1", read_events=lambda sid: RED)
red = view("s1")["decision"] or {}
check("a question the turn ended on with [[SF:RED]], with no Telegram at all",
      red.get("reason") == "要先修哪個？" and [o["n"] for o in red.get("options", [])] == [1, 2], red)
bridge_home.forget("s1")

print("chat_view: rev")
a = view("s1")
b = view("s1", a["rev"])
check("nothing changed: unchanged", b == {"success": True, "unchanged": True, "rev": a["rev"]}, b)
with open(CLAUDE, "a", encoding="utf-8") as f:
    f.write(rec("user", "謝謝", "2026-11-10T10:01:00Z") + "\n")
c = view("s1", a["rev"])
check("the record grew: the whole view again", not c.get("unchanged") and c["rev"] != a["rev"]
      and c["turns"][-1]["text"] == "謝謝", c.get("rev"))
STATUS["s1"] = {"state": "working"}
check("the state changed: the view again", not view("s1", c["rev"]).get("unchanged"))
STATUS["s1"] = {"state": "done"}

print("chat_send: the phone's message box")
sent = []


def fake_deliver(api_, sid, text, **kw):
    sent.append((sid, text, kw))
    return {"success": True, "queued": sid == "s2", "position": 1 if sid == "s2" else None,
            "message": "queued" if sid == "s2" else "sent"}


real_deliver = tab_delivery.deliver
tab_delivery.deliver = fake_deliver
r = json.loads(api.chat_send("s1", "幫我\n看一下"))
check("into tab_delivery, as typed, submitted, not cutting in",
      sent and sent[-1][0] == "s1" and sent[-1][1] == "幫我\n看一下" and sent[-1][2].get("submit") is True
      and sent[-1][2].get("now") is False and r == {"success": True, "queued": False, "position": None, "message": "sent"}, (sent, r))
q = json.loads(api.chat_send("s2", "等一下做這個"))
check("a busy tab: queued, and the view is told", q["success"] and q["queued"] and q["position"] == 1, q)
n = len(sent)
e = json.loads(api.chat_send("s1", "   \n "))
check("nothing to send: nothing sent", e == {"success": False, "code": "empty"} and len(sent) == n, e)
x = json.loads(api.chat_send("s9", "hi"))
check("no such tab: refused", not x["success"] and len(sent) == n, x)
tab_delivery.deliver = real_deliver

print("chat_answer: /link/answer's path")
tg = []
bridge_home._tg = lambda bridge, method, payload: tg.append((method, payload))
api.bridge = types.SimpleNamespace(slots={"s3": types.SimpleNamespace(pending_menu=True, pending_menu_options=[1])})
qid = v3["decision"]["id"]
tab = api.sessions["s3"]
stale = json.loads(api.chat_answer("s3", 1, "not-this-question"))
check("another question's id: refused, nothing typed", stale["code"] == "stale" and tab.written == [], stale)
no = json.loads(api.chat_answer("s3", 7, qid))
check("an option that is not listed: refused, nothing typed", no["code"] == "no_option" and tab.written == [], no)
bridge_home._open["s3"] = {"text": "要繼續嗎？", "copies": [(111, 222)]}
ok = json.loads(api.chat_answer("s3", 2, qid))
check("the right question: option 2 and Enter, nothing else", ok["success"] and tab.written == ["2\r"], (ok, tab.written))
check("…Telegram's copy says it was answered on the computer",
      tg and tg[-1][0] == "editMessageText" and "已在電腦上選：2" in tg[-1][1]["text"]
      and api.bridge.slots["s3"].pending_menu is False, tg)
again = json.loads(api.chat_answer("s3", 2, qid))
check("a second tap right after: refused", again["code"] == "stale" and tab.written == ["2\r"], again)
bridge_home._answered_at.clear()
bridge_home._open["s3"] = {"text": "要繼續嗎？", "copies": [(111, 333)]}
tab.written.clear()
phone_res = api._execute_sfctl("bots_answer", {"sid": "s3", "n": 1, "id": qid})
check("the phone's own answer still says 'on the phone'", phone_res.get("success") and "已在手機上選：1" in tg[-1][1]["text"]
      and tab.written == ["1\r"], (phone_res, tg[-1]))

print("an older menu in the scrollback is not offered")
# Measured on a Codex tab: its start-up update menu stays in the scrollback after it is
# answered. The shared reader takes 40 lines above the screen and returns the first menu.
OLD_MENU = ["  ✨ Update available! 0.142.1 -> 0.167.0", "", "› 1. Update now (runs `npm install -g @openai/codex`)",
            "  2. Skip", "  3. Skip until next version", "", "  Press enter to continue"]
NEW_MENU = ["  Would you like to run the following command?", "", "  $ touch codex-probe.txt", "",
            "› 1. Yes, proceed (y)", "  2. Yes, and don't ask again for this command in this session (a)",
            "  3. No, and tell Codex what to do differently (esc)", "", "  Press enter to confirm or esc to cancel"]
api.sessions["s6"] = Tab("s6", "codex -a untrusted")
api.sessions["s6"]._tmux_name = "sf_s6"
STATUS["s6"] = {"state": "decision"}
SCREENS["s6"] = "\n".join(OLD_MENU + ["", "› count to 3", ""] + NEW_MENU)
VISIBLE = {"s6": "\n".join(NEW_MENU)}
api._chat_visible_screen = lambda sid: VISIBLE.get(sid)
shared = api._link_decision("s6", "decision", {"state": "decision"}) or {}
check("(the shared reader returns the old update menu: reported, not changed here)",
      [o["text"] for o in shared.get("options", [])][:1] == ["Update now (runs `npm install -g @openai/codex`)"], shared)
v6 = view("s6")
check("the chat view does not offer it: no buttons, it says why", v6["decision"] is None
      and v6.get("decision_hidden") == "not_on_screen" and v6["activity"] == "decision", v6.get("decision"))
w = json.loads(api.chat_answer("s6", 2, shared.get("id", "")))
check("…and refuses to answer it: nothing typed", w["code"] == "not_on_screen" and api.sessions["s6"].written == [], w)
SCREENS["s6"] = "\n".join(NEW_MENU)
on = view("s6")["decision"] or {}
check("the menu that is on screen is offered", [o["n"] for o in on.get("options", [])] == [1, 2, 3]
      and on.get("options", [{}])[0].get("text") == "Yes, proceed (y)", on)
VISIBLE["s6"] = "\n".join(NEW_MENU[:5])
check("…unless one of its options is not on screen", view("s6")["decision"] is None)
bridge_home.on_turn_end(api, "s4", read_events=lambda sid: RED)
STATUS["s4"] = {"state": "done"}
VISIBLE["s4"] = "$ "
check("a [[SF:RED]] question needs no screen", ((view("s4")["decision"] or {}).get("reason")) == "要先修哪個？")
bridge_home.forget("s4")

print("web/chat.js only calls what the backend has")
src = (HERE / "web" / "chat.js").read_text(encoding="utf-8")
calls = set(re.findall(r"(?:api\(\)|\ba)\.([a-z_]+)\(", src))
missing = sorted(c for c in calls if not callable(getattr(Api, c, None)))
check(f"all {len(calls)} js_api calls exist ({', '.join(sorted(calls))})", calls and not missing, missing)
check("the view's three routes are on the Api", all(callable(getattr(Api, m, None)) for m in ("chat_view", "chat_send", "chat_answer")))
check("the providers the page draws are the backend's", "const CHAT_PROVIDERS = ['claude', 'codex']" in src
      and api_chat.CHAT_PROVIDERS == ("claude", "codex"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
