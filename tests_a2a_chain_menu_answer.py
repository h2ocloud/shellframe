#!/usr/bin/env python3
"""Answering a tab's question by button starts a fresh A2A chain in that tab (v0.44.5).

Reported: a tab got an A2A message 5 layers deep (shown as reference only), then raised
[[SF:RED]] with options; the person pressed "1" on the desktop's chat view, and what the tab
sent next was still layer 6, over the limit of 4, and not delivered. The log read "answered 1 from
the desktop ('1')": every option button (the desktop chat view's, a phone's /link/answer and
Telegram's) types its answer through tab_delivery.answer_choice straight into the PTY, which never
cleared the chain, and a menu takes the digit alone, so there was no Enter either.

Each way the person answers, the tab starts over at layer 1 and can send again:
  - the desktop chat view's option button (the reported path), a phone's, Telegram's choice:
    button for an [[SF:RED]] question and for a menu on screen
  - Telegram's other answering buttons: a model, an effort level, the usage-limit menu, trust
  - an option's number pressed alone in the terminal, or sent as raw keys (/link/input:
    a phone's terminal, a remote pane on another computer)
  - a phone's send (/link/send from a peer of kind "ios")
What stays: a tap refused as stale, a button that answers nothing, typing a word without Enter,
and a send that is not the person's (an agent's sfctl send, link-send from another computer).
Run: .venv/bin/python tests_a2a_chain_menu_answer.py
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-chain-answer-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")          # fake tabs stay out of the real log

import agent_link  # noqa: E402
agent_link.LOG_FILE = TMP / "a2a_log.json"                    # out of the real A2A log
import bridge_home as bh  # noqa: E402
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "shellframe_bridge.log")              # out of the real bridge log
import frame_link  # noqa: E402
import main  # noqa: E402
import tab_delivery  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": True}}), encoding="utf-8")

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


sent_tg = []
bt.tg_api = lambda token, method, payload=None, **k: sent_tg.append((method, payload)) or {"ok": True}
bh.SEND = lambda method, payload: sent_tg.append((method, payload)) or {"ok": True}
tab_delivery.ANSWER_SETTLE_S = 0.3
ROSTER = {"秘書": {"label": "秘書"}, "研究": {"label": "研究"}}
SCREEN = {"s1": ""}
tab_delivery.visible_text = lambda api, sid: SCREEN.get(sid, "")
bt.menu_block = lambda lines: ([{"text": ln} for ln in lines if ln.strip()], 0)   # every line counts as an option


def deep(sid="s1"):
    """The reported state: the tab got a message 5 layers deep, so it would send layer 6."""
    agent_link._inbound_depth.clear()
    agent_link._recent.clear()
    agent_link.note_delivered(sid, agent_link.MAX_DEPTH + 1)
    return agent_link.depth_for(sid) == agent_link.MAX_DEPTH + 2


def can_send(sid="s1"):
    agent_link._recent.clear()
    ok, why, depth = agent_link.authorize(enabled=True, from_sid=sid, from_label="秘書", to_role="研究",
                                          to_sid="", text="新的事", roster=ROSTER)
    return ok is True and depth == 1


def api_with_tab(sid="s1"):
    api = Api()
    written = []

    def write(d):
        written.append(d)
        SCREEN[sid] = ""                                 # a menu takes the digit and goes away

    s = types.SimpleNamespace(
        sid=sid, cmd="claude", cwd="~", lock=MagicMock(), write=write,
        _recent=bytearray(), _startup_trust_pending=False, _last_activity_time=0.0,
        _last_user_activity_time=0.0, _idle_reap_state="", _slug_pending=False, _tmux_name=None,
        _init_pending=False, _master_turn=False)
    api.sessions = {sid: s}
    api.bridge = None
    api._arm_awaiting_response = lambda *a, **k: None
    api._auto_name = lambda *a, **k: None
    api._push_answered = lambda *a, **k: None
    return api, s, written


def asking(api, kind, qid="q1"):
    """The tab's question as /link/bots and the chat view see it."""
    api._agent_status_snapshot = lambda sid: ({"state": "decision"}, None)
    api._link_decision = lambda sid, state, res: {"id": qid, "kind": kind, "text": "要繼續嗎？",
                                                  "options": [{"n": 1, "text": "好"}, {"n": 2, "text": "先不要"}]}
    bh._answered_at.clear()
    SCREEN["s1"] = "1. 好\n2. 先不要" if kind == "menu" else ""


print("0. the reported state")
check("a tab that got a message 5 layers deep would send layer 6, and is stopped", deep() and not can_send())

print("1. the desktop chat view's option button (the reported path)")
api, s, written = api_with_tab()
deep()
asking(api, "menu")
res = json.loads(api.chat_answer("s1", 1, "q1"))
check("the answer goes in as the digit alone, as the log showed ('1')", res.get("success") and written == ["1"],
      (res, written))
check("…and the tab starts over: layer 1, and it may send again", agent_link.depth_for("s1") == 1 and can_send(),
      agent_link._inbound_depth)


class Ctx:
    """What agent_link.deliver needs: s1 (the tab that answered) and s2, both open."""
    def __init__(self):
        self.sent = []

    def enabled(self):
        return True

    def resolve(self, role, sid):
        return {"秘書": ("s1", "秘書"), "研究": ("s2", "研究")}.get(role, ("", ""))

    def roster(self):
        return ROSTER

    def send(self, sid, text):
        self.sent.append((sid, text))
        return {"success": True}


ctx = Ctx()
agent_link._recent.clear()
r = agent_link.deliver(ctx, role="研究", sid="", text="照你剛才選的做", from_sid="s1", from_label="秘書")
check("end to end: an A2A send from that tab now arrives, as layer 1",
      r.get("success") and ctx.sent and agent_link.history(1)[-1].get("depth") == 1, (r, agent_link.history(1)))

print("2. a phone's option button (/link/answer)")
api, s, written = api_with_tab()
deep()
asking(api, "red")
res = api._execute_sfctl("bots_answer", {"sid": "s1", "n": 2, "id": "q1", "peer_id": "p-phone"}) or {}
check("an [[SF:RED]] question: the number and Enter go in", res.get("success") and written == ["2\r"], (res, written))
check("…and the chain starts over", agent_link.depth_for("s1") == 1 and can_send())
deep()
res = api._execute_sfctl("bots_answer", {"sid": "s1", "n": 1, "id": "an-old-question", "peer_id": "p-phone"}) or {}
check("an answer to a question that is gone sends nothing and leaves the chain",
      res.get("code") == "stale" and not agent_link.depth_for("s1") == 1, res)

print("3. Telegram's buttons")


def bridge_for(api, written):
    b = bt.TelegramBridge("tg", bt.TelegramBridgeConfig(bot_token="x", allowed_users=[]))
    b._prune_stale_slots = lambda force=False: None
    b.register_session("s1", "秘書", lambda text: written.append(text) or SCREEN.update(s1=""), cmd="claude")
    b.home_api = api
    return b


def tap(b, data, qid, message_id):
    b._handle_callback_query({"id": qid, "data": data, "from": {"id": 100},
                              "message": {"message_id": message_id, "chat": {"id": 100}}})


api, s, _ = api_with_tab()
tg_written = []
b = bridge_for(api, tg_written)
bh._open.clear(); bh._answered.clear(); bh._retired.clear(); bh._answered_at.clear()
deep()
tap(b, "choice:s1:1", "a", 10)
check("choice: on an [[SF:RED]] question: \"1\" and Enter go in, the chain starts over",
      tg_written == ["1\r"] and agent_link.depth_for("s1") == 1 and can_send(), (tg_written, agent_link._inbound_depth))
deep()
tap(b, "choice:s1:1", "b", 10)
check("a second tap on the same message is refused and leaves the chain",
      tg_written == ["1\r"] and agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 2)
tg_written.clear()
deep()
b.slots["s1"].pending_menu = True
SCREEN["s1"] = "1. 是\n2. 否"
tap(b, "choice:s1:2", "c", 11)
check("choice: on a menu on screen: the digit goes in, the chain starts over",
      tg_written == ["2"] and agent_link.depth_for("s1") == 1, (tg_written, agent_link._inbound_depth))
tg_written.clear()
deep()
tap(b, "rlchoice:s1:1", "d", 12)
check("the usage-limit menu's button: the chain starts over", tg_written == ["1\r"] and agent_link.depth_for("s1") == 1,
      tg_written)
for data in ("mchoice:s1:2", "efchoice:claude:s1:high", "trust:s1:yes"):
    deep()
    bh.person_tapped(b, data)
    check(f"{data.split(':')[0]}: the chain starts over", agent_link.depth_for("s1") == 1, agent_link._inbound_depth)
for data in ("close:s1", "new:claude", "choice:s9:1", "tgnow:abc"):
    deep()
    bh.person_tapped(b, data)
    check(f"{data}: not an answer to this tab, the chain stays", agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 2)
src = (HERE / "bridge_telegram.py").read_text(encoding="utf-8")
h = src[src.index("def _handle_callback_query"):]
check("the bridge calls it after the stale check and before any button types",
      h.index("bridge_home.on_choice(") < h.index("bridge_home.person_tapped(self, data)") < h.index("slot.write_fn("))

print("4. keys from the person")
api, s, written = api_with_tab()
deep()
api.write_input("s1", "幫我看")
check("a word typed without Enter leaves the chain", agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 2)
api.write_input("s1", "1")
check("an option's number pressed alone in the terminal answers the menu: the chain starts over",
      agent_link.depth_for("s1") == 1)
deep()
res = api._execute_sfctl("raw_input", {"sid": "s1", "data": "abc"}) or {}
check("raw keys (/link/input) without Enter or an option's number leave the chain",
      res.get("success") and agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 2, res)
api._execute_sfctl("raw_input", {"sid": "s1", "data": "2"})
check("…an option's number from a phone's terminal or a remote pane starts it over", agent_link.depth_for("s1") == 1)
deep()
api._execute_sfctl("raw_input", {"sid": "s1", "data": "\r"})
check("…and so does Enter", agent_link.depth_for("s1") == 1)
check("answers(): Enter, or one option digit", agent_link.answers("\r") and agent_link.answers("3")
      and not agent_link.answers("0") and not agent_link.answers("12") and not agent_link.answers("\x1b[A")
      and not agent_link.answers(""))

print("5. a phone's send")
calls = []
fake = types.SimpleNamespace(
    peers=lambda: {"p-phone": {"name": "示範 iPad", "kind": "ios"}, "p-mac": {"name": "示範電腦", "kind": ""}},
    _execute=lambda cmd, args: calls.append((cmd, args)) or {"success": True},
    _record_inbox=lambda ev: None)
frame_link.FrameLink._local_send(fake, "s1", "1", True, source_peer="p-phone")
frame_link.FrameLink._local_send(fake, "s1", "照做", True, source_peer="p-mac")
check("/link/send says a phone's send is the person's; another computer's (link-send) is not",
      calls[0][1].get("from_phone") is True and calls[1][1].get("from_phone") is False, calls)
api, s, written = api_with_tab()
real_deliver = tab_delivery.deliver
tab_delivery.deliver = lambda *a, **k: {"success": True}
try:
    deep()
    api._execute_sfctl("send", {"sid": "s1", "text": "照做", "submit": True})
    check("a send that is not the person's (an agent's sfctl send, link-send) leaves the chain",
          agent_link.depth_for("s1") == agent_link.MAX_DEPTH + 2)
    api._execute_sfctl("send", {"sid": "s1", "text": "1", "submit": True, "from_phone": True})
    check("a phone's send starts it over", agent_link.depth_for("s1") == 1)
finally:
    tab_delivery.deliver = real_deliver

print("6. LINE")
line = (HERE / "bridge_line.py").read_text(encoding="utf-8")
check("a LINE message from the person starts the chain over before it is typed in",
      0 < line.index("agent_link.clear_chain(sid)") < line.index("slot.write_fn(payload)"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
