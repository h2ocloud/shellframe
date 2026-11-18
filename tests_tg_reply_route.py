#!/usr/bin/env python3
"""A Telegram "Reply" to a push goes to whoever made the push (tg_reply_route.py), through
the real bridge with a stub Bot API.

  - what the bridge sends for a tab (a reply, `sfctl tg-send`, a choice question) is recorded
    with its message id; a stub answer without one records nothing
  - a reply to a tab's message goes to that tab: the selected tab and the person's pick stay,
    a bot that sleeps is woken, a reused tab id is not trusted, the tab's answer reaches the chat
  - a reply to a schedule's push goes to the bot `owners` names (the schedule's group too)
  - nobody known: the bots as buttons and "the current tab", a tap delivers, a held message expires
  - the rules file names the owner of a push nobody registered
  - what the tab reads: the reply's context, the original's first 300 characters, then the words;
    a bare digit answering a numbered menu goes in as it is
  - files and a voice message follow the reply (read once, not again after a wake)
  - messages without reply_to, replies to the person's own messages, a failure in the router and
    slash commands behave as before
  - `tg_push`, the app's side of `sfctl tg-push`

Run: .venv/bin/python tests_tg_reply_route.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-tg-route-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ.setdefault("SHELLFRAME_TESTS", "1")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")

import bridge_home as bh  # noqa: E402
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = os.path.join(tempfile.mkdtemp(prefix="sf-blog-"), "shellframe_bridge.log")
import sf_config  # noqa: E402
import tg_origins as to  # noqa: E402
import tg_reply_route as rr  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


BOT_ID, CHAT, USER = 4242, 1100, 100
sent = []
_mid = [1000]


def api(method, payload=None, **k):
    sent.append((method, payload))
    if method == "sendMessage":
        _mid[0] += 1
        return {"ok": True, "result": {"message_id": _mid[0], "chat": {"id": (payload or {}).get("chat_id")}}}
    return {"ok": True}


bh.SEND = api
bh.CACHE_S = 0
bt.tg_api = lambda token, method, payload=None, **k: api(method, payload)
bt.bridge_queue.wait_turn = lambda *a, **k: "ready"
bt.voice_apply_gate = lambda: False


class Home:
    """The app as the bridge sees it: three bots (ops and 研究 awake, 巡檢 asleep) and one outside ShellBot."""
    def __init__(self, b):
        self.b = b
        self.roles = {"s2": "研究", "s3": "ops"}
        self.labels = {"ops": "ops", "研究": "研究", "巡檢": "巡檢", "ext": "ext"}
        self.external = {"ext"}
        self.opened = []
        self.bridge = b

    def _bots_enabled(self):
        return True

    def _bots_primary_tab(self, cfg=None):
        return "", "", ""

    def _bots_role_of(self, sid, cfg=None):
        return self.roles.get(sid, "")

    def _awake(self, role):
        return next((s for s, r in self.roles.items() if r == role and s in self.b.slots), "")

    def _bots_list_for_sfctl(self):
        rows = []
        for role, label in self.labels.items():
            sid = self._awake(role)
            state = "external" if role in self.external else ("awake" if sid else "asleep")
            rows.append({"role": role, "label": label, "state": state, "sid": sid})
        return {"success": True, "details": {"bots": rows}}

    def _resolve_name(self, name):
        want = str(name).casefold()
        for role in self.labels:
            if role.casefold() == want:
                return {"ok": True, "role": role, "sid": self._awake(role)}
        if name in self.b.slots:
            return {"ok": True, "role": self.roles.get(name, ""), "sid": name}
        return {"ok": False, "error": "not_found"}

    def bots_open(self, role):
        self.opened.append(role)
        sid = self._awake(role)
        if not sid:
            sid = f"s{9 + len(self.opened) - 1}"
            self.roles[sid] = role
            make_tab(self.b, sid, role)
        return json.dumps({"success": True, "details": {"sid": sid}})

    def is_session_ready_for_bridge(self, sid):
        return True


written = defaultdict(list)


def make_tab(b, sid, label):
    b.register_session(sid, label, lambda t, sid=sid: written[sid].append(t), cmd="claude")


_last = [None]


def fresh():
    """A bridge with tab s1 (a plain tab, the person's pick), s2 (研究), s3 (ops)."""
    if _last[0] is not None:       # the last test's typing threads end first: a late Enter is not the next test's
        wait_for(lambda: not any(getattr(s, "inject_pending", False) for s in list(_last[0].slots.values())), 10)
    rr.reset()
    written.clear()
    sent.clear()
    b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[USER]))
    b._prune_stale_slots = lambda force=False: None
    b.bot_info = {"id": BOT_ID, "username": "sfbot"}
    for sid, label in (("s1", "Coding"), ("s2", "研究"), ("s3", "ops")):
        make_tab(b, sid, label)
    b.home_api = Home(b)
    _last[0] = b
    b._user_chat[USER] = CHAT
    b._user_active[USER] = "s1"
    b._react_async = lambda *a, **k: None
    b._wait_input_safe = lambda s: ""
    b._live_tail = lambda s: ""
    b._verify_injection = lambda *a, **k: (True, False)
    b._wait_paste_drain = lambda *a, **k: None
    return b


def bot_msg(mid, text, chat=CHAT):
    return {"message_id": mid, "from": {"id": BOT_ID, "is_bot": True}, "chat": {"id": chat}, "text": text}


def user_msg(mid, text="hi"):
    return {"message_id": mid, "from": {"id": USER}, "chat": {"id": CHAT}, "text": text}


def upd(text="", reply=None, mid=None, **extra):
    _mid[0] += 1
    m = {"message_id": mid or _mid[0], "text": text, "from": {"id": USER, "username": "u"},
         "chat": {"id": CHAT, "type": "private"}}
    if reply is not None:
        m["reply_to_message"] = reply
    m.update(extra)
    return {"update_id": _mid[0], "message": m}


def typed(sid):
    return "".join(written[sid])


def say(b, u):
    b._handle_update(u)


def texts():
    return [p.get("text", "") for m, p in sent if m == "sendMessage" and p]


def acks():
    return [t for t in texts() if t.startswith("→")]


def keyboard():
    for m, p in sent:
        if m == "sendMessage" and p and (p.get("reply_markup") or {}).get("inline_keyboard"):
            rows = p["reply_markup"]["inline_keyboard"]
            if rows[0][0]["callback_data"].startswith("rr:"):
                return p, rows
    return None, []


def tap(b, data, mid=900):
    b._handle_callback_query({"id": "cq", "data": data, "from": {"id": USER},
                              "message": {"chat": {"id": CHAT}, "message_id": mid}})


SCHEDULES = sf_config.config_dir() / "schedules.json"
RULES = sf_config.config_dir() / to.RULES_NAME
for p in (SCHEDULES, RULES):
    try:
        p.unlink()
    except OSError:
        pass

print("recording what the bridge sends for a tab")
b = fresh()
b._send_text_checked("s2", CHAT, "研究結果：一切正常")
to.flush()
e = to.lookup(CHAT, _mid[0])
check("a tab's reply is recorded with its tab, its bot and the start of the text",
      e and e["kind"] == "tab" and e["sid"] == "s2" and e["bot"] == "研究" and e["head"].startswith("研究結果"), e)
b._send_text_checked("s1", CHAT, "plain tab")
to.flush()
e = to.lookup(CHAT, _mid[0])
check("a plain tab has no bot", e and e["sid"] == "s1" and "bot" not in e, e)
n_before = len(to._read())
bt.tg_api = lambda token, method, payload=None, **k: {"ok": True}          # the stubs of other tests answer so
b._send_text_checked("s1", CHAT, "no message id in the answer")
to.flush()
check("an answer without a message id records nothing", len(to._read()) == n_before)
bt.tg_api = lambda token, method, payload=None, **k: api(method, payload)
b.home_api._bots_primary_tab = lambda cfg=None: ("ops", "s3", "ops")
bh._sent.clear()
bh.RATE_PER_HOUR = 20
res = bh.tg_send(b.home_api, "里程碑完成", "s3")
b.active = True
res = bh.tg_send(b.home_api, "里程碑完成", "s3")
to.flush()
e = to.lookup(CHAT, _mid[0])
check("`sfctl tg-send` is recorded as the sending tab's", res.get("success") and e and e["sid"] == "s3" and e["bot"] == "ops", (res, e))
bh.opened(b, "s2", "要不要發版？", CHAT, {"ok": True, "result": {"message_id": 5555}})
to.flush()
check("a choice question is recorded as its tab's", (to.lookup(CHAT, 5555) or {}).get("sid") == "s2")
b.home_api._bots_primary_tab = lambda cfg=None: ("", "", "")

print("the other tab sends, and a recorder that fails")
b = fresh()
b.slots["s2"].pending_menu_options = []
b._send_choice_menu(CHAT, b.slots["s2"], "選一個動作")
to.flush()
check("a menu question with no buttons (it invites a digit) is recorded", (to.lookup(CHAT, _mid[0]) or {}).get("sid") == "s2")
b._peek_last_response = lambda slot: "最新回覆內容"
b._handle_command("fetch", USER, CHAT, "/fetch")
to.flush()
check("/fetch's copy of a tab's reply is recorded as that tab's", (to.lookup(CHAT, _mid[0]) or {}).get("sid") == "s1")
slot = b.slots["s2"]
b._detect_and_fire_signal(slot, ["[[SF:GREEN]]"])
to.flush()
e = to.lookup(CHAT, _mid[0])
check("a done banner is recorded as its tab's", e and e["sid"] == "s2" and e["head"].startswith("✅ 研究"), (e, texts()[-1:]))
b._notify_rate_limit(slot, {"reset": "3pm", "interactive": True})
to.flush()
check("a rate-limit notice is recorded as its tab's", (to.lookup(CHAT, _mid[0]) or {}).get("sid") == "s2")
b._on_answer_dialog = lambda *a: True
slot.trust_prompt_sent = False
b._offer_trust_buttons("s2", CHAT, "研究")
to.flush()
check("a trust question is recorded as its tab's", (to.lookup(CHAT, _mid[0]) or {}).get("sid") == "s2")
slot.msg_sent_ts = time.time() - 300
b._heartbeat_status_line = lambda sid: "working"
b._maybe_preview = lambda slot, waited: ""
b._user_active[USER] = "s2"           # a heartbeat goes to whoever has the tab selected
b._send_heartbeat("s2", 120)
to.flush()
check("a heartbeat is recorded as its tab's", (to.lookup(CHAT, _mid[0]) or {}).get("sid") == "s2" and "還在跑" in texts()[-1], texts()[-1:])
real_note = to.note
to.note = lambda *a, **k: 1 / 0
ok = b._send_text_checked("s2", CHAT, "紀錄壞掉了")
to.note = real_note
check("a recorder that raises does not turn a delivered reply into a failed send (it would be sent again)", ok == (True, 0.0, False), ok)
src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
check("the bridge calls nothing new on bridge_home (a reload without a restart still works)",
      "bridge_home.note_tab" not in src and "bridge_home.wake_and_replay" not in src)

print("a reply to a tab's message goes to that tab")
b = fresh()
to.record(CHAT, 7001, to.tab_origin("s2", "研究"), "上一輪的報告")
say(b, upd("好，就照這個做", reply=bot_msg(7001, "研究結果：已完成第一階段，請確認。" * 30)))
check("it is typed into that tab", wait_for(lambda: "好，就照這個做" in typed("s2")), written)
t = typed("s2")
check("…with the context, the original's first 300 characters and the person's words",
      "（Telegram：這是對這個分頁先前訊息的回覆）" in t and "原訊息：「研究結果：已完成第一階段" in t
      and "…」" in t and "使用者的回覆：好，就照這個做" in t, t[:400])
check("…the quote is cut at 300 characters", len(t.split("原訊息：「")[1].split("」")[0]) == 301)
time.sleep(0.3)
check("the selected tab got nothing", typed("s1") == "")
check("the person's pick did not change", b._user_active[USER] == "s1" and b.get_active_sid(USER) == "s1")
check("Telegram says one line: handed to 研究", acks() == ["→ 已交給 研究"], texts())
check("its answer reaches this chat though it is not the selected tab",
      CHAT in b._target_chats_for("s2") and CHAT in b._target_chats_for("s1") and b._user_active[USER] == "s1")
ok, _, _ = b._send_text_checked("s2", CHAT, "收到")
check("…and a send to it goes out", ok and texts()[-1] == "收到")
rr._guests["s2"][CHAT] = time.time() - 1
check("the one-off ends after a while: a tab that is not selected stops reaching the chat", CHAT not in b._target_chats_for("s2"))

print("…when the origin is the selected tab: the old flow, nothing added")
b = fresh()
to.record(CHAT, 7002, to.tab_origin("s1"), "plain")
say(b, upd("接著做", reply=bot_msg(7002, "完成了")))
check("typed into the selected tab as a normal message", wait_for(lambda: "接著做" in typed("s1")))
check("…without the context and without an ack", "使用者的回覆" not in typed("s1") and not acks())

print("…a bot that sleeps is woken, and the message goes in once it is up")
b = fresh()
to.record(CHAT, 7003, to.tab_origin("s8", "巡檢"), "巡檢報告")          # s8 closed long ago: the bot sleeps
fetched = []
b._fetch_media = lambda media, ext, chat_id, label: fetched.append(1) or "/tmp/sf-route-test.png"
say(b, upd("", caption="這張圖是錯的", photo=[{"file_id": "p"}], reply=bot_msg(7003, "巡檢報告：3 個異常")))
check("Telegram says it is being woken", wait_for(lambda: any("叫醒中" in a for a in acks())), texts())
check("the bot was woken", wait_for(lambda: b.home_api.opened == ["巡檢"]), b.home_api.opened)
check("the message is queued again for the bridge", wait_for(lambda: b._update_queue.qsize() == 1))
again = b._update_queue.get()
check("…carrying its text, its file and its route, so nothing is downloaded or transcribed twice",
      again["message"]["text"] == "這張圖是錯的" and again["message"]["_sf_files"] == ["/tmp/sf-route-test.png"]
      and again["message"]["_sf_route"]["role"] == "巡檢" and len(fetched) == 1, again)
say(b, again)
new_sid = next(s for s, r in b.home_api.roles.items() if r == "巡檢")
check("typed into the woken bot's tab, file path included",
      wait_for(lambda: "這張圖是錯的" in typed(new_sid) and "/tmp/sf-route-test.png" in typed(new_sid)), written)
check("…the file was fetched once", len(fetched) == 1)
check("the selected tab and pick are untouched", typed("s1") == "" and b._user_active[USER] == "s1",
      (typed("s1")[:200], b._user_active))
check("only the one ack line", len(acks()) == 1, acks())

print("…a reused tab id is not trusted")
b = fresh()
to.record(CHAT, 7004, to.tab_origin("s2", "巡檢"), "巡檢的舊訊息")        # s2 was 巡檢 once; now it is 研究
say(b, upd("再跑一次", reply=bot_msg(7004, "巡檢：完成")))
check("it goes to the bot, not to whoever has that id now", wait_for(lambda: b.home_api.opened == ["巡檢"]) and typed("s2") == "", written)
b._update_queue.get_nowait()

print("a reply to a schedule's push goes to its owner bot")
b = fresh()
SCHEDULES.write_text(json.dumps({"prefixes": ["demo."], "owners": {"demo.report": "ops", "group:demo.nightly": "研究"},
                                 "groups": {"demo.nightly": ["demo.n-a", "demo.n-b"]}}), encoding="utf-8")
to.record(CHAT, 7101, to.schedule_origin("demo.report"), "報表失敗")
long_push = "報表失敗：看板 API 連不上 (HTTP 401)。" + "細節" * 200
say(b, upd("先看 token 有沒有過期", reply=bot_msg(7101, long_push)))
check("typed into the owner's tab", wait_for(lambda: "先看 token 有沒有過期" in typed("s3")), written)
t = typed("s3")
check("…as a reply to that schedule's push, with the push's first 300 characters",
      "（Telegram：這是對排程 demo.report 推播的回覆）" in t and "原推播：「報表失敗：看板 API 連不上 (HTTP 401)。" in t
      and "…」" in t and "使用者的回覆：先看 token 有沒有過期" in t, t[:300])
check("…acked, and the pick is unchanged", acks() == ["→ 已交給 ops"] and b._user_active[USER] == "s1" and typed("s1") == "", (acks(), typed("s1")))
b = fresh()
to.record(CHAT, 7102, to.schedule_origin("demo.n-b"), "夜間批次")
say(b, upd("繼續", reply=bot_msg(7102, "夜間批次完成")))
check("a member of a group goes to the group's owner", wait_for(lambda: "繼續" in typed("s2")), written)
b = fresh()
to.record(CHAT, 7103, to.schedule_origin("demo.report"), "報表")
b._user_active[USER] = "s3"
say(b, upd("好", reply=bot_msg(7103, "報表失敗")))
check("the owner is the selected tab: the context goes in, no ack", wait_for(lambda: "使用者的回覆：好" in typed("s3")) and not acks(), (acks(), written))

print("…its answer reaches a user with no pick of their own")
b = fresh()
b._user_active.clear()
b._default_active_sid = "s3"
to.record(CHAT, 7110, to.schedule_origin("demo.report"), "報表")
check("the fallback tab is the one the owner has, and that is not their home tab", b.get_active_sid(USER) == "s3" and CHAT not in b._target_chats_for("s3"))
say(b, upd("好", reply=bot_msg(7110, "報表失敗")))
check("the owner's tab is their fallback: the message goes in, and its answer would still reach them",
      wait_for(lambda: "使用者的回覆：好" in typed("s3")) and CHAT in b._target_chats_for("s3") and USER not in b._user_active)

print("a bot that was deleted is not woken")
b = fresh()
to.record(CHAT, 7111, to.tab_origin("s8", "已刪除的bot"), "舊訊息")
say(b, upd("還在嗎", reply=bot_msg(7111, "舊訊息")))
check("a tab of a bot that is gone: asked, not woken", keyboard()[0] is not None and not b.home_api.opened)

print("nobody known: who gets this?")
b = fresh()
to.record(CHAT, 7201, to.schedule_origin("demo.unowned"), "沒人認領的推播")
say(b, upd("這是什麼", reply=bot_msg(7201, "沒人認領的推播：磁碟 91%")))
p, rows = keyboard()
check("a question with the bots as buttons and the current tab", p and "這則要交給誰？" in p["text"]
      and [r[0]["text"] for r in rows] == ["🤖 ops", "🤖 研究", "💤 巡檢", "➡ 目前分頁"], rows)
check("…not the one outside ShellBot", not any("ext" in r[0]["text"] for r in rows))
check("…every callback fits Telegram's 64 bytes", all(len(r[0]["callback_data"].encode()) <= 64 for r in rows))
time.sleep(0.3)
check("nothing was typed anywhere yet", not any(written.values()))
tap(b, rows[3][0]["callback_data"])
check("the current tab: the message goes in with the context", wait_for(lambda: b._update_queue.qsize() == 1))
say(b, b._update_queue.get())
check("…typed into the selected tab, as a reply to a push", wait_for(lambda: "使用者的回覆：這是什麼" in typed("s1")) and "這是對排程 demo.unowned 推播的回覆" in typed("s1"), written)
check("…the question message says it", any(m == "editMessageText" and p["text"] == "→ 已交給 Coding" for m, p in sent),
      [p for m, p in sent if m == "editMessageText"])
tap(b, rows[3][0]["callback_data"])
check("a second tap on the same buttons is too old", any(m == "answerCallbackQuery" and "過期" in (p or {}).get("text", "") for m, p in sent))

b = fresh()
to.record(CHAT, 7202, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("交給研究", reply=bot_msg(7202, "沒人認領的推播")))
p, rows = keyboard()
tap(b, rows[1][0]["callback_data"])
b2 = b._update_queue.get(timeout=3)
say(b, b2)
check("a bot's button: its tab gets it, the selected tab does not", wait_for(lambda: "交給研究" in typed("s2")) and typed("s1") == "")
check("…and the pick is unchanged", b._user_active[USER] == "s1")

b = fresh()
to.record(CHAT, 7203, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("叫巡檢", reply=bot_msg(7203, "沒人認領的推播")))
p, rows = keyboard()
tap(b, rows[2][0]["callback_data"])
check("a sleeping bot's button: woken, then it goes in", wait_for(lambda: b.home_api.opened == ["巡檢"]) and wait_for(lambda: b._update_queue.qsize() == 1))
say(b, b._update_queue.get())
check("…typed into the woken tab", wait_for(lambda: "叫巡檢" in typed(next(s for s, r in b.home_api.roles.items() if r == "巡檢"))), written)

b = fresh()
to.record(CHAT, 7204, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("太晚了", reply=bot_msg(7204, "沒人認領")))
p, rows = keyboard()
for h in rr._held.values():
    h["at"] -= rr.HELD_TTL_S + 5
tap(b, rows[3][0]["callback_data"])
time.sleep(0.3)
check("a held message expires (10 minutes): the tap says so and nothing is sent",
      any(m == "editMessageText" and "已過期" in p["text"] for m, p in sent) and b._update_queue.qsize() == 0 and not any(written.values()))
check("…and nothing is held any more", not rr._held)

b = fresh()
b.home_api._bots_enabled = lambda: False
to.record(CHAT, 7205, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("沒有 bot 可選", reply=bot_msg(7205, "沒人認領的推播")))
check("bots off: no one to choose, so the current tab gets it with the context and an ack",
      wait_for(lambda: "使用者的回覆：沒有 bot 可選" in typed("s1")) and acks() == ["→ 已交給 Coding"] and keyboard()[0] is None, (acks(), written))

print("a message the registry does not know")
b = fresh()
say(b, upd("這是誰的", reply=bot_msg(7301, "一則誰也不認得的推播")))
check("sent by the bot but unknown and no rule: the same question", keyboard()[0] is not None)

print("the rules file names the owner of a legacy push")
b = fresh()
RULES.write_text(json.dumps([{"prefix": "🌆 Nightly report", "schedule": "demo.report"}, {"prefix": "Backup", "bot": "研究"}]), encoding="utf-8")
say(b, upd("看一下", reply=bot_msg(7302, "  🌆 Nightly report failed: 401")))
check("a schedule rule: the schedule's owner", wait_for(lambda: "看一下" in typed("s3")) and "排程 demo.report 推播" in typed("s3"), written)
b = fresh()
say(b, upd("備份呢", reply=bot_msg(7303, "Backup finished")))
check("a bot rule: that bot, without the schedule's owner", wait_for(lambda: "備份呢" in typed("s2")) and "這是對一則推播的回覆" in typed("s2"), written)
b = fresh()
say(b, upd("其他", reply=bot_msg(7304, "Something else")))
check("no rule matches: asked", keyboard()[0] is not None)
RULES.unlink()

print("the old flow stays")
b = fresh()
say(b, upd("沒有回覆的訊息"))
check("no reply_to: the selected tab, as a normal message",
      wait_for(lambda: "沒有回覆的訊息" in typed("s1")) and "使用者的回覆" not in typed("s1") and not acks() and keyboard()[0] is None)
b = fresh()
say(b, upd("回我自己的話", reply=user_msg(7401, "我先前打的")))
check("a reply to the person's own message: the selected tab, as before",
      wait_for(lambda: "回我自己的話" in typed("s1")) and "使用者的回覆" not in typed("s1") and keyboard()[0] is None)
b = fresh()
other = bot_msg(7402, "another bot's message")
other["from"]["id"] = 999
say(b, upd("另一個 bot", reply=other))
check("a reply to another bot's message: as before", wait_for(lambda: "另一個 bot" in typed("s1")) and keyboard()[0] is None)
b = fresh()
to.record(CHAT, 7403, to.tab_origin("s2", "研究"), "x")
say(b, upd("/compact", reply=bot_msg(7403, "研究結果")))
check("a slash command in a reply goes to the selected tab raw, not routed",
      wait_for(lambda: "/compact" in typed("s1")) and "使用者的回覆" not in typed("s1") and typed("s2") == "")
b = fresh()
to.record(CHAT, 7404, to.tab_origin("s2", "研究"), "x")
orig = rr._resolve
rr._resolve = lambda *a, **k: 1 / 0
say(b, upd("路由壞掉了", reply=bot_msg(7404, "研究結果")))
rr._resolve = orig
check("a failure in the router falls back to the selected tab (the message is not lost)",
      wait_for(lambda: "路由壞掉了" in typed("s1")) and typed("s2") == "")
b = fresh()
to.record(CHAT, 7405, to.tab_origin("s2", "研究"), "x")
b.slots["s2"].pending_menu = True
say(b, upd("1", reply=bot_msg(7405, "要繼續嗎？\n1. 是\n2. 否")))
check("a bare digit for a tab that shows a numbered menu goes in as it is (it answers the menu)",
      wait_for(lambda: "1" in typed("s2")) and "使用者的回覆" not in typed("s2"), typed("s2"))
b = fresh()
to.record(CHAT, 7406, to.tab_origin("s2", "研究"), "x")
b.paused = True
b.config.auto_resume_on_message = False
say(b, upd("暫停中", reply=bot_msg(7406, "研究結果")))
time.sleep(0.3)
check("a paused bridge ignores it, replies included", not any(written.values()), dict(written))

print("a voice message in a reply")
b = fresh()
bt.voice_apply_gate = lambda: True
b._download_tg_file = lambda fid, ext="": "/tmp/sf-route-test.oga"
b._transcribe_voice = lambda path: "對，就這樣"
b._refine_transcript = lambda t: t
b._refine_settings = lambda: {"enabled": False}
to.record(CHAT, 7501, to.schedule_origin("demo.report"), "報表")
say(b, upd("", voice={"file_id": "v"}, reply=bot_msg(7501, "報表失敗")))
vap = next((r[0]["callback_data"] for m, p in sent if m == "sendMessage" and p and "Apply" in str(p.get("reply_markup")) for r in p["reply_markup"]["inline_keyboard"]), "")
check("the transcript waits for Apply", vap.startswith("vapply:"), texts())
tap(b, vap)
check("after Apply it still goes to the push's owner", wait_for(lambda: "對，就這樣" in typed("s3")) and "排程 demo.report" in typed("s3") and typed("s1") == "", written)
bt.voice_apply_gate = lambda: False

print("sfctl tg-push, the app's side")
b = fresh()
b.active = True
app = b.home_api
app.bridge = b
b.config.allowed_users = []              # the one chat is the bound one (a user id is also a private chat id)
sent.clear()
res = rr.tg_push(app, "報表完成\n一切正常", "demo.report")
to.flush()
check("it is sent to the person's chat", res["success"] and any(m == "sendMessage" and p["chat_id"] == CHAT and "報表完成" in p["text"] for m, p in sent), res)
e = to.lookup(CHAT, _mid[0])
check("…and registered as that schedule's", e and e["kind"] == "schedule" and e["id"] == "demo.report", e)
sent.clear()
res = rr.tg_push(app, "x" * 9000, "demo.report")
to.flush()
check("a long text is split in three", res["success"] and res["details"]["sent"] == 3
      and len([1 for m, p in sent if m == "sendMessage"]) == 3, res)
ids = [_mid[0] - 2, _mid[0] - 1, _mid[0]]
check("…each part is findable", all((to.lookup(CHAT, i) or {}).get("id") == "demo.report" for i in ids), ids)
check("a chat ShellBot does not talk to is refused", not rr.tg_push(app, "hi", "demo.report", chat="555")["success"])
check("a chat it does talk to is fine", rr.tg_push(app, "hi", "demo.report", chat=str(CHAT))["success"])
b.active = False
check("Telegram off: refused, so the script can send itself", not rr.tg_push(app, "hi", "demo.report")["success"])
b.active = True
check("no text or no schedule: refused", not rr.tg_push(app, " ", "demo.report")["success"] and not rr.tg_push(app, "hi", "")["success"])
bh.SEND = lambda method, payload: {"ok": False, "description": "Too Many Requests"}
res = rr.tg_push(app, "hi", "demo.report")
check("Telegram refuses: not sent, and nothing counted as sent", not res["success"] and not res["details"]["sent"], res)
bh.SEND = api
src = (HERE / "api_remote.py").read_text(encoding="utf-8")
check("the app routes the sfctl command to it", 'cmd == "tg_push"' in src and "tg_reply_route.tg_push(" in src)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
