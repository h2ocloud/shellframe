#!/usr/bin/env python3
"""A Telegram reply that is routed leaves one line in the bridge log (tg_reply_route._arrive), so
acceptance can be read from /tmp/shellframe_bridge.log: `[reply-route] → <name> sid=<sid> by=<how>`.

  by=registry  the replied-to message is in the registry (a tab's)
  by=rule      tg_reply_rules.json named the owner of a push nobody registered
  by=owner     a schedule's push, to the bot `owners` names (awake, or woken: one line, when it is up)
  by=button    the person's tap on "who gets this?"
  by=current   nobody to ask: the selected tab
A reply to the tab it is already going to is not routed and logs nothing; no line holds the message text.

Run: .venv/bin/python tests_tg_reply_route_log.py
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


LOG = bt._LOG_FILE


def route_lines():
    try:
        return [ln for ln in open(LOG, encoding="utf-8").read().splitlines() if "[reply-route] →" in ln]
    except OSError:
        return []


def clear_log():
    try:
        open(LOG, "w").close()
    except OSError:
        pass


def last_by():
    ls = route_lines()
    return ls[-1].split("[reply-route] →", 1)[1].strip() if ls else ""


SCHEDULES.write_text(json.dumps({"prefixes": ["demo."], "owners": {"demo.report": "ops"}}), encoding="utf-8")

print("registry: a reply to a tab's message")
clear_log()
b = fresh()
to.record(CHAT, 7001, to.tab_origin("s2", "研究"), "上一輪的報告")
say(b, upd("好，就照這個做", reply=bot_msg(7001, "研究結果：已完成")))
check("one line: the tab, its sid, by=registry", wait_for(lambda: len(route_lines()) == 1) and last_by() == "研究 sid=s2 by=registry", route_lines())
check("the line holds no message text", "好，就照這個做" not in route_lines()[0] and "研究結果" not in route_lines()[0], route_lines())

print("owner: a schedule's push, bot awake")
clear_log()
b = fresh()
to.record(CHAT, 7101, to.schedule_origin("demo.report"), "報表失敗")
say(b, upd("先看 token", reply=bot_msg(7101, "報表失敗")))
check("by=owner, the owner's tab", wait_for(lambda: len(route_lines()) == 1) and last_by() == "ops sid=s3 by=owner", route_lines())

print("owner: the bot sleeps — woken, and one line when it is up")
clear_log()
b = fresh()
to.record(CHAT, 7003, to.tab_origin("s8", "巡檢"), "巡檢報告")
say(b, upd("再跑一次", reply=bot_msg(7003, "巡檢報告：3 個異常")))
check("woken", wait_for(lambda: b.home_api.opened == ["巡檢"]) and wait_for(lambda: b._update_queue.qsize() == 1))
check("nothing is logged while it is still coming up", route_lines() == [], route_lines())
say(b, b._update_queue.get())
new_sid = next(s for s, r in b.home_api.roles.items() if r == "巡檢")
check("once it is up: one line with the new tab", wait_for(lambda: len(route_lines()) == 1) and last_by() == f"巡檢 sid={new_sid} by=registry", route_lines())
time.sleep(0.2)
check("…and only one", len(route_lines()) == 1, route_lines())

print("rule: a push nobody registered")
clear_log()
b = fresh()
RULES.write_text(json.dumps([{"prefix": "Backup", "bot": "研究"}]), encoding="utf-8")
say(b, upd("備份呢", reply=bot_msg(7303, "Backup finished")))
check("by=rule", wait_for(lambda: len(route_lines()) == 1) and last_by() == "研究 sid=s2 by=rule", route_lines())

print("button: who gets this?")
clear_log()
b = fresh()
to.record(CHAT, 7202, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("交給研究", reply=bot_msg(7202, "沒人認領的推播")))
p, rows = keyboard()
check("asked, nothing logged yet", p is not None and route_lines() == [], route_lines())
tap(b, rows[1][0]["callback_data"])
say(b, b._update_queue.get(timeout=3))
check("by=button", wait_for(lambda: len(route_lines()) == 1) and last_by() == "研究 sid=s2 by=button", route_lines())

print("current: nobody to choose among")
clear_log()
b = fresh()
b.home_api._bots_enabled = lambda: False
to.record(CHAT, 7205, to.schedule_origin("demo.unowned"), "沒人認領")
say(b, upd("沒有 bot 可選", reply=bot_msg(7205, "沒人認領的推播")))
check("by=current, the selected tab", wait_for(lambda: len(route_lines()) == 1) and last_by() == "Coding sid=s1 by=current", route_lines())

print("not routed: no line")
clear_log()
b = fresh()
to.record(CHAT, 7002, to.tab_origin("s1"), "plain")
say(b, upd("接著做", reply=bot_msg(7002, "完成了")))
check("a reply to the selected tab's own message", wait_for(lambda: "接著做" in typed("s1")))
time.sleep(0.2)
check("…logs nothing", route_lines() == [], route_lines())
say(b, upd("沒回覆就是一般訊息"))
time.sleep(0.2)
check("a message that is not a reply logs nothing", route_lines() == [], route_lines())

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
sys.exit(1 if failed else 0)
