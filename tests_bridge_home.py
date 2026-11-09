#!/usr/bin/env python3
"""The primary bot on Telegram: where a message lands, how a decision reaches the
phone, binding a phone, and the primary bot's own pushes.

Decided: one shared Telegram bot with the primary bot as the default (decision
1), woken when it sleeps (2), milestones by tg-send and decisions pushed, quiet
hours off by default and, when on, decisions only (3), other bots' decisions
pushed too with their name and buttons that answer them (4), and binding into an
empty allowlist locks it to the person who bound (6).

  - routing: your own /N pick while that tab is open, else the primary bot
    (also when your tab closed), else the old fallbacks; /home and /0 go back
  - a sleeping primary is woken and the message handled once its tab is up
  - a bot's [[SF:RED]] outside a Telegram turn becomes buttons on every phone;
    plain tabs and Telegram turns keep their old behaviour
  - tg-send: primary only, 20 an hour, not in quiet hours (overnight ranges too)
  - /start <code> binds before the allowlist check, once, until it expires;
    the empty allowlist is then locked to that person

Run: .venv/bin/python tests_bridge_home.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tg-home-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")  # fake tabs stay out of the real log

import bridge_home as bh  # noqa: E402
import bridge_telegram as bt  # noqa: E402
import bridge_telegram as _bt_log  # noqa: E402
_bt_log._LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="sf-blog-"), "shellframe_bridge.log")  # fake tabs stay out of the real bridge log
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}, "agent_roster": {
    "主控": {"label": "主控", "cmd": "claude", "bot": {"primary": True, "memory": True}}}}), encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


sent = []
bh.CACHE_S = 0                           # every check sees the state it just set
bh.PUSH_DEDUP_S = 0                      # the de-duplication has its own check
bh.SEND = lambda method, payload: sent.append((method, payload)) or {"ok": True}
bt.tg_api = lambda token, method, payload=None, **k: sent.append((method, payload)) or {"ok": True}
SETTINGS = {"default_primary": True, "quiet": {"on": False, "from": "23:00", "to": "08:00"}}
real_settings = bh.settings
bh.settings = lambda api: SETTINGS


class Home:
    """The app as bridge_home sees it."""
    def __init__(self):
        self.primary = ("主控", "s1", "主控")
        self.roles = {"s1": "主控", "s2": "研究"}
        self.opened, self.bound, self.ready = [], [], True
        self.bridge = None

    def _bots_primary_tab(self, cfg=None):
        return self.primary

    def _bots_role_of(self, sid, cfg=None):
        return self.roles.get(sid, "")

    def bots_open(self, role):
        self.opened.append(role)
        return json.dumps({"success": True, "details": {"sid": "s9"}})

    def is_session_ready_for_bridge(self, sid):
        return self.ready

    def _tg_bind_user(self, uid):
        self.bound.append(uid)
        self.bridge.config.allowed_users = list(self.bridge.config.allowed_users or []) + [uid]


def make_bridge(*tabs):
    b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]))
    b._prune_stale_slots = lambda force=False: None
    for sid, label in tabs:
        b.register_session(sid, label, lambda text: None, cmd="claude")
    b.home_api = Home()
    b.home_api.bridge = b
    return b


print("where a message lands")
b = make_bridge(("s3", "Coding"), ("s1", "主控"), ("s2", "研究"))
b._default_active_sid = "s3"
check("no pick of your own: the primary bot, not the desktop's tab or the first", b.get_active_sid(100) == "s1")
b._user_active[100] = "s2"
check("your own /N pick wins while that tab is open", b.get_active_sid(100) == "s2")
b._user_active[100] = "s7"
check("your tab closed: back to the primary bot", b.get_active_sid(100) == "s1")
b._user_chat.update({100: 1100, 200: 1200})
b._user_active.pop(100)
check("its replies reach users with no pick (the primary is home now)",
      1100 in b._target_chats_for("s1") and 1100 not in b._target_chats_for("s3"))
SETTINGS["default_primary"] = False
check("switched off in the settings: the old fallback (the desktop's tab)", b.get_active_sid(100) == "s3")
SETTINGS["default_primary"] = True
b.home_api.primary = ("", "", "")
check("no primary bot: the old fallback", b.get_active_sid(100) == "s3")
b.home_api.primary = ("主控", "s1", "主控")

print("/home")
b._user_active[100] = "s2"
sent.clear()
check("/home: back to the primary bot", bh.handle_command(b, "home", 100, 1100) and b.get_active_sid(100) == "s1"
      and "回到 ⭐ 主控" in sent[-1][1]["text"])
b._user_active[100] = "s2"
check("/0 does the same", bh.handle_command(b, "0", 100, 1100) and b.get_active_sid(100) == "s1")
check("other commands are not ours", not bh.handle_command(b, "list", 100, 1100))
src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
check("the bridge asks first, and /home is routed to it",
      "bridge_home.handle_command(self, cmd, user_id, chat_id)" in src and "'groups', 'home') or cmd.isdigit()" in src)

print("a sleeping primary bot")
b = make_bridge(("s3", "Coding"))
b.home_api.primary = ("主控", "", "主控")
sent.clear()
upd = {"message": {"text": "盤點一下", "from": {"id": 100}, "chat": {"id": 1100}}}
check("a message with no pick wakes it", bh.wake_for(b, 100, 1100, upd) and wait_for(lambda: b.home_api.opened == ["主控"]))
check("…and says so", "正在叫醒 ⭐ 主控" in sent[0][1]["text"])
upd2 = {"message": {"text": "還有這個", "from": {"id": 100}, "chat": {"id": 1100}}}
check("a second message while it wakes waits too, without a second wake",
      bh.wake_for(b, 100, 1100, upd2) and b.home_api.opened == ["主控"])
b.register_session("s9", "主控", lambda text: None, cmd="claude")
b.home_api.primary = ("主控", "s9", "主控")
got = []
check("once its tab is up, both messages are handled again, in order",
      wait_for(lambda: b._update_queue.qsize() == 2) and [b._update_queue.get()["message"]["text"]
                                                            for _ in range(2)] == ["盤點一下", "還有這個"])
b._user_active[100] = "s3"
b.home_api.primary = ("主控", "", "主控")
check("with a pick of your own, nothing is woken", not bh.wake_for(b, 100, 1100, upd))
check("the bridge calls it before it picks a tab", src.index("bridge_home.wake_for(self, user_id, chat_id, update)")
      < src.index("active_sid = self.get_active_sid(user_id)\n        # Ensure user is tracked"))

print("a decision reaches the phone")
b = make_bridge(("s1", "主控"), ("s2", "研究"), ("s4", "shell"))
b._user_chat.update({100: 1100})
b.config.allowed_users = [100, 300]
menus = []
b._send_choice_menu = lambda chat_id, slot, text: menus.append((chat_id, slot.sid, text))
b._detect_menu_prompt = lambda slot: "❓ 待決策：請選一個動作\n1. 先修\n2. 先發"
bh.push_decision(b, b.slots["s2"])
check("another bot's decision: to every bound phone, its name first, buttons that answer it",
      sorted(c for c, _, _ in menus) == [100, 300, 1100] and all(s == "s2" and t.startswith("🤖 研究 需要你決定")
                                                                 for _, s, t in menus), menus)
menus.clear()
bh.push_decision(b, b.slots["s1"])
check("the primary bot's: a star", menus and menus[0][2].startswith("⭐ 主控 需要你決定"))
menus.clear()
bh.push_decision(b, b.slots["s4"])
check("a plain tab: unchanged (nothing pushed)", menus == [])
b._detect_menu_prompt = lambda slot: ""
sent.clear()
bh.push_decision(b, b.slots["s2"], "要不要先停機")
check("no numbered options: the question as text", any("要不要先停機" in p["text"] for _, p in sent))
pushed = []
real_push = bh.push_decision
bh.push_decision = lambda bridge, slot, reason="": pushed.append(slot.sid)
b._signal_desktop_notify = lambda *a, **k: None
slot = b.slots["s2"]
slot.has_user_msg, slot.last_signal = False, ""
b._detect_and_fire_signal(slot, ["[[SF:RED]]"])
check("the bridge pushes a RED raised outside a Telegram turn", pushed == ["s2"], pushed)
slot.has_user_msg, slot.last_signal = True, ""
b._detect_and_fire_signal(slot, ["[[SF:RED]]"])
check("…and leaves a Telegram turn's to the old menu push", pushed == ["s2"], pushed)
bh.push_decision = real_push

print("a decision at the end of a turn (read from the transcript)")
# Measured on the phone: the primary bot ended an A2A-started turn on [[SF:RED]] with two
# options and nothing arrived; the bridge still had an earlier Telegram turn open on that tab,
# so its own detection never looked. The Stop hook now reads the turn's last words.
A2A = {"kind": "user_msg", "text": "[[A2A：來自「sf dev」的訊息]] 請驗收"}
RED = {"kind": "assistant_text", "text": "驗收步驟如下。\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知"}
check("a turn ending on [[SF:RED]]: the question and its options",
      bh.red_block([A2A, RED]) == ("要先修哪個？", [("1", "先修按鈕"), ("2", "先修通知")]), bh.red_block([A2A, RED]))
check("a Telegram-started turn is left to the bridge",
      bh.red_block([{"kind": "user_msg", "text": "請放在 [[TG_REPLY_ab12cd34]] 之間"}, RED]) is None)
check("no [[SF:RED]]: nothing", bh.red_block([A2A, {"kind": "assistant_text", "text": "做完了。"}]) is None)
check("RED with no options: the question alone",
      bh.red_block([A2A, {"kind": "assistant_text", "text": "可以發布嗎？\n[[SF:RED]]"}]) == ("可以發布嗎？", []))
check("an older turn's RED does not count", bh.red_block([A2A, RED, {"kind": "user_msg", "text": "下一步"},
                                                          {"kind": "assistant_text", "text": "好"}]) is None)
api = Home()
b = make_bridge(("s1", "主控"), ("s2", "研究"), ("s4", "shell"))
api.bridge = b
b.home_api = api
b.active = True
b.config.allowed_users = [100]
sent.clear()
bh.on_turn_end(api, "s1", read_events=lambda sid: [A2A, RED])
btn = [pl for m, pl in sent if m == "sendMessage" and pl.get("reply_markup")]
check("pushed with buttons that answer that tab",
      btn and btn[0]["text"].startswith("⭐ 主控 需要你決定") and "要先修哪個？" in btn[0]["text"]
      and [r[0]["callback_data"] for r in btn[0]["reply_markup"]["inline_keyboard"]] == ["choice:s1:1", "choice:s1:2"],
      sent)
sent.clear()
bh.on_turn_end(api, "s4", read_events=lambda sid: [A2A, RED])
check("a plain tab: nothing", not sent)
bh.PUSH_DEDUP_S = 30.0
bh._pushed_at.clear()
bh.on_turn_end(api, "s2", read_events=lambda sid: [A2A, RED])
n = len(sent)
bh.push_decision(b, b.slots["s2"])
check("the same decision is not pushed twice (the turn-end path and the bridge's own)", n == 1 and len(sent) == 1, sent)
bh.PUSH_DEDUP_S = 0
app = Api.__new__(Api)
app.sessions, app._hook_events, app._status_cache = {}, {}, {}
app.bridge = b
called = []
real_end = bh.on_turn_end
bh.on_turn_end = lambda a, sid, read_events=None: called.append(sid)
app._on_agent_event({"sid": "s1", "event": "Stop"})
check("the Stop hook event starts that check", wait_for(lambda: called == ["s1"]), called)
# Measured: the call was gated on the bridge, so a computer without Telegram never kept a
# [[SF:RED]] question, and the phone's /link/bots decision stayed empty exactly there.
app.bridge = None
app._on_agent_event({"sid": "s2", "event": "Stop"})
check("…with no Telegram bridge too (the question is kept for the phone)", wait_for(lambda: called == ["s1", "s2"]), called)
app.bridge = b
bh.on_turn_end = real_end

print("a choice counts once")
# Measured: "choice:s344:1" was tapped twice (the decision, then an older copy while a newer
# question was out) and "1" went into the tab twice.
mids = iter(range(500, 600))
real_send = bh.SEND
calls = []
def fake_send(method, payload):
    calls.append((method, payload))
    return {"ok": True, "result": {"message_id": next(mids)}} if method == "sendMessage" else {"ok": True}
bh.SEND = fake_send
api = Home()
b = make_bridge(("s1", "主控"))
api.bridge = b
b.home_api = api
b.active = True
b.config.allowed_users = [100, 300]
bh._open.clear(); bh._answered.clear(); bh._retired.clear()
bh.on_turn_end(api, "s1", read_events=lambda sid: [A2A, RED])
copies = bh._open["s1"]["copies"]
check("the question went to both chats and both copies are remembered", sorted(c for c, _ in copies) == [100, 300], copies)
(c1, m1), (c2, m2) = sorted(copies)
calls.clear()
check("the first tap goes in", bh.on_choice(b, "choice:s1:1", c1, m1, "q1") is False)
check("…and the other copy loses its buttons",
      any(m == "editMessageReplyMarkup" and p["message_id"] == m2 and p["reply_markup"] == {"inline_keyboard": []}
          for m, p in calls), calls)
calls.clear()
check("a second tap on it is refused, and says why", bh.on_choice(b, "choice:s1:1", c1, m1, "q2") is True
      and any(m == "answerCallbackQuery" and "回答過了" in p["text"] for m, p in calls), calls)
check("…so is a tap on the other copy", bh.on_choice(b, "choice:s1:2", c2, m2, "q3") is True)
bh.on_turn_end(api, "s1", read_events=lambda sid: [A2A, {"kind": "assistant_text", "text": "第一題的回覆"}])
bh._pushed_at.clear()
bh.on_turn_end(api, "s1", read_events=lambda sid: [A2A, RED])
older = list(bh._open["s1"]["copies"])
bh._pushed_at.clear()
calls.clear()
bh.on_turn_end(api, "s1", read_events=lambda sid: [A2A, {"kind": "assistant_text",
                                                         "text": "新的題目\n[[SF:RED]]\n1. 甲\n2. 乙"}])
check("a newer question retires the older one's buttons",
      sum(1 for m, p in calls if m == "editMessageReplyMarkup" and (p["chat_id"], p["message_id"]) in older) == 2, calls)
calls.clear()
check("…and a tap on the older one is refused, saying a newer question is out",
      bh.on_choice(b, "choice:s1:1", *older[0], "q4") is True
      and any(m == "answerCallbackQuery" and "新的題目" in p["text"] for m, p in calls), calls)
check("other buttons are not ours", bh.on_choice(b, "tgnow:abc", 100, 1, "q5") is False)
bh._open.clear(); bh._answered.clear(); bh._retired.clear()
written = []
rb = make_bridge(("s1", "主控"))
rb.slots["s1"].write_fn = lambda text: written.append(text)
rb.home_api = api
sent.clear()
bh.SEND = real_send
cq = lambda qid: {"id": qid, "data": "choice:s1:1", "from": {"id": 100}, "message": {"message_id": 77, "chat": {"id": 100}}}
rb._handle_callback_query(cq("a"))
rb._handle_callback_query(cq("b"))
check("through the bridge: two taps on one message, one \"1\" typed in", written == ["1\r"], written)
check("…and the second tap is told it was answered",
      any(m == "answerCallbackQuery" and "回答過了" in str((p or {}).get("text", "")) for m, p in sent), sent[-3:])
bh.SEND = real_send

print("saying it is connected")
b = make_bridge(("s1", "主控"), ("s2", "研究"))
b.config.allowed_users = [100]
b.bot_info = {"username": "demo_shellbot"}
sent.clear()
bh.announce_start(b)
msgs = [pl for m, pl in sent if m == "sendMessage"]
check("a person connecting: one quiet message with the right count and /home",
      len(msgs) == 1 and msgs[0].get("disable_notification") and "2 個分頁" in msgs[0]["text"]
      and "/home" in msgs[0]["text"] and "none" not in msgs[0]["text"], msgs)
b.announce_start = False
sent.clear()
bh.announce_start(b)
check("the app's own restart or a reload: nothing on the phone", not sent)
asrc = (HERE / "api_bridges.py").read_text(encoding="utf-8")
msrc = (HERE / "main.py").read_text(encoding="utf-8")
check("the restore path starts the bridge without announcing, and so does a reload",
      '"", announce=False)' in asrc and "self.bridge.announce_start = False" in msrc
      and "Sessions: {sessions_info}" not in src)

print("tg-send")
b = make_bridge(("s1", "主控"), ("s2", "研究"))
b._user_chat.update({100: 1100})
b.config.allowed_users = [100, 300]
api = Home()
api.bridge = b
b.home_api = api
bh._sent.clear()
b.active = True                          # as if the bridge were polling
r = bh.tg_send(api, "done", "s2")
check("only from the primary bot's tab", not r["success"] and "主要 Bot" in r["message"], r)
sent.clear()
r = bh.tg_send(api, "第 1 到 7 項完成", "s1")
check("the primary bot reaches every bound phone", r["success"] and sorted(p["chat_id"] for _, p in sent) == [100, 300, 1100]
      and all(p["text"] == "⭐ 主控：第 1 到 7 項完成" for _, p in sent), sent)
for _ in range(bh.RATE_PER_HOUR - 1):
    bh.tg_send(api, "x", "s1")
check(f"at most {bh.RATE_PER_HOUR} an hour", not bh.tg_send(api, "one too many", "s1")["success"])
bh._sent.clear()
SETTINGS["quiet"] = {"on": True, "from": "00:00", "to": "23:59"}
check("quiet hours: not sent (decisions still go out)", not bh.tg_send(api, "late", "s1")["success"])
SETTINGS["quiet"] = {"on": False, "from": "23:00", "to": "08:00"}
b.active = False
check("Telegram not connected: says so", not bh.tg_send(api, "x", "s1")["success"])
b.active = True

print("quiet hours")
at = lambda hh, mm: time.mktime(time.struct_time((2026, 11, 9, hh, mm, 0, 0, 313, -1)))
SETTINGS["quiet"] = {"on": True, "from": "23:00", "to": "08:00"}
check("overnight range: 23:30 and 07:59 are quiet, 08:00 and 12:00 are not",
      bh.quiet_now(api, at(23, 30)) and bh.quiet_now(api, at(7, 59))
      and not bh.quiet_now(api, at(8, 0)) and not bh.quiet_now(api, at(12, 0)))
SETTINGS["quiet"] = {"on": True, "from": "12:00", "to": "13:00"}
check("daytime range", bh.quiet_now(api, at(12, 30)) and not bh.quiet_now(api, at(13, 30)))
SETTINGS["quiet"] = {"on": False, "from": "00:00", "to": "23:59"}
check("off by default means never", not bh.quiet_now(api, at(12, 0)))

print("binding a phone")
b = make_bridge(("s1", "主控"))
b._set_bot_commands = lambda: None
p = bh.pair_start()
sent.clear()
check("a wrong code is not a pairing", not bh.try_pair(b, 555, 5550, "/start nope"))
check("/start <code> binds that person", bh.try_pair(b, 555, 5550, f"/start {p['code']}")
      and b.home_api.bound == [555] and b._user_chat[555] == 5550 and "已綁定" in sent[-1][1]["text"])
check("the empty allowlist is now locked to them", b.config.allowed_users == [555])
check("a code works once", not bh.try_pair(b, 556, 5560, f"/start {p['code']}"))
p = bh.pair_start(ttl=0.01)
time.sleep(0.05)
check("…and not after it expires", not bh.try_pair(b, 557, 5570, f"/start {p['code']}"))
check("plain /start is left to the old handler", not bh.try_pair(b, 555, 5550, "/start"))
check("the bridge checks the code before the allowlist",
      src.index("bridge_home.try_pair(self, user_id, chat_id, text)") < src.index('"text": "Access denied.",'))

print("through the bridge's own message handler")
b = make_bridge(("s1", "主控"), ("s3", "Coding"))
b._set_bot_commands = lambda: None
b._react_async = lambda *a, **k: None
b.config.allowed_users = [100]
msg = lambda mid, text, uid: {"update_id": mid, "message": {"message_id": mid, "text": text, "date": int(time.time()),
                                                            "from": {"id": uid, "username": f"u{uid}"},
                                                            "chat": {"id": uid, "type": "private"}}}
p = bh.pair_start()
sent.clear()
b._handle_update(msg(1, f"/start {p['code']}", 555))
check("a stranger's /start <code> binds them, no 'Access denied'", 555 in b.config.allowed_users
      and not any("Access denied" in str((pl or {}).get("text", "")) for _, pl in sent), sent[-2:])
sent.clear()
b._handle_update(msg(2, "/home", 100))
check("/home through the handler", any("回到 ⭐ 主控" in str((pl or {}).get("text", "")) for _, pl in sent), sent[-2:])
bh.CACHE_S = 2.0                          # as in the app: the cache is what could re-wake it
bh._cache["at"] = 0.0
b.home_api.primary = ("主控", "", "主控")
b._user_active.pop(100, None)
sent.clear()
b._handle_update(msg(3, "盤點一下", 100))
check("a plain message while it sleeps: it is woken and the message kept",
      any("正在叫醒" in str((pl or {}).get("text", "")) for _, pl in sent) and wait_for(lambda: b.home_api.opened))
b.register_session("s9", "主控", lambda text: None, cmd="claude")
b.home_api.primary = ("主控", "s9", "主控")
check("…once it is up the message is handled again", wait_for(lambda: b._update_queue.qsize() == 1))
again = b._update_queue.get()
check("…and that second pass does not wake it again (the cached 'asleep' is gone)",
      not bh.wake_for(b, 100, 100, again) and b.get_active_sid(100) == "s9")
bh.CACHE_S = 0

print("cost")
bh.CACHE_S = 2.0
calls = []
cb = make_bridge(("s1", "主控"))
cb.home_api._bots_primary_tab = lambda cfg=None: calls.append(1) or ("主控", "s1", "主控")
for _ in range(200):
    cb._target_chats_for("s1")
check("the reply loop asking 200 times reads the primary bot once (cached 2 s)", len(calls) == 1, len(calls))
bh.CACHE_S = 0

print("who a bound person is")
chats = {555: {"id": 555, "first_name": "Ann", "username": "ann_dev"}, 556: {"id": 556, "first_name": "Bo", "last_name": "Li"}}
real_send = bh.SEND
bh.SEND = lambda method, payload: ({"ok": True, "result": chats[payload["chat_id"]]}
                                   if method == "getChat" and payload["chat_id"] in chats else {"ok": False})
check("@username when they have one", bh.user_label(b, 555) == "@ann_dev")
check("their name and id when they do not", bh.user_label(b, 556) == "Bo Li · 556")
check("the bare id when Telegram cannot say", bh.user_label(b, 557) == "557")
calls = []
bh.SEND = lambda method, payload: calls.append(method) or {"ok": True, "result": chats[555]}
bh.user_label(b, 555)
check("asked once, then remembered (the panel asks every 2 s)", calls == [])
bh.SEND = real_send

print("the app side")
bh.settings = real_settings
app = Api.__new__(Api)
app.sessions = {}
app.bridge = None
app._tg_bind_user(777)
cfg = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
check("binding writes the allowlist (empty → only that person)", cfg["bridge"]["allowed_users"] == [777])
app._tg_bind_user(888)
app._tg_bind_user(777)
check("a second person is added once", json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["bridge"]["allowed_users"]
      == [777, 888])
st = json.loads(app.tg_home_set(json.dumps({"default_primary": False, "quiet": {"on": True, "from": "22:30"}})))
check("settings: only what is given changes", st["default_primary"] is False and st["quiet"]["on"] is True
      and st["quiet"]["from"] == "22:30" and st["quiet"]["to"] == "08:00", st)
st = json.loads(app.tg_unbind(888))
check("unbinding removes one person", st["users"] == [777] and st["allow_all"] is False, st)
check("the status names the primary bot", st["primary"] == "主控", st)
check("no bridge: pairing is refused with a reason", not json.loads(app.tg_pair_start())["success"])

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
