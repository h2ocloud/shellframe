#!/usr/bin/env python3
"""Every place that shows one line of a message shows it plain: no A2A header, no paste wrapper, no Telegram marker.

tests_msg_preview.py checks the rule (agent_link.preview_of, web/chat.js preview). This runs the real code
that makes each line, on the message shapes measured on a real tab (_paste_shapes.py) and on a transcript file
written the way Claude Code writes one. The source of nearly all of them is agent_status._detail (`task`, the
person's last message, and `narration`, the agent's last words), so a line that used to carry
"[[A2A：來自「研究」的訊息，不是使用者本人。請自行判斷是否採納，必要時可用 [[SF:TO:<角色|…" now says
"研究：比較表整理好了…":

  - the transcript → events → _detail (a string or a list of text blocks as `content`)
  - the phone's GET /link/bots `latest`, the list's `agent_activity` (/link/info, /link/state), `sfctl bots`
  - Telegram: the heartbeat's status line, /list under a tab (a screen as Claude Code draws it, through the
    real _peek_last_response)
  - a [[SF:RED]] question (bridge_home.red_block): the phone's `decision` and the Telegram buttons, when the
    reply it sits in opens with its [[TG_REPLY_…]] on the question's line or closes on an option's
  - a push notification's body (the last words of a turn that ended on a Telegram reply)
  - the webhook's "Turn finished: …"

Demo words only. Run: .venv/bin/python tests_msg_preview_sinks.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-preview-sinks-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "shellframe_debug.log")
import bridge_telegram as bt  # noqa: E402
bt._LOG_FILE = str(TMP / "shellframe_bridge.log")
import agent_link  # noqa: E402
import agent_status as ag  # noqa: E402
import api_push  # noqa: E402
import bridge_home  # noqa: E402
import main  # noqa: E402
import sf_notify  # noqa: E402
import tab_delivery  # noqa: E402
import _paste_shapes as ps  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True}, "agent_roster": {}}), encoding="utf-8")
tab_delivery.screen_text = lambda api, sid, lines=60: ""
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


WRAPPERS = ["[[A2A", "pasted_content", "TG_REPLY", "[[SF:", "不是使用者本人", "請自行判斷是否採納", "最終要回 Telegram"]


def clean(line):
    return not [w for w in WRAPPERS if w in str(line)]


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.cmd, self.alive = sid, label, "claude", True
        self._tmux_name, self._recent, self.lock = None, bytearray(), threading.Lock()
        self._last_output_activity_time = 0.0


def status_of(events, state):
    """What agent_status._status_for_impl returns from a transcript's events, for the fields a line is made of."""
    action, task, narration = ag._detail(events)
    return {"state": state, "summary": action or narration or state, "action": action, "narration": narration, "task": task}


def make_api(res):
    api = Api.__new__(Api)
    api.sessions = {"s1": Tab("s1", "示範分頁")}
    api.bridge = api.line_bridge = None
    api._window = None
    api._agent_status_snapshot = lambda sid: (dict(res, prompt_at=0.0), 0)
    api._agent_state_for_list = lambda sid: res.get("state", "")
    api._agent_blocked_for_list = lambda sid: ""
    api._last_activity_at = lambda sid, s, out_ts=0.0: 0.0
    api._ordered_sids = lambda cfg=None, preferred_order=None: ["s1"]
    return api


def heartbeat_line(res):
    br = object.__new__(bt.TelegramBridge)
    br.config = types.SimpleNamespace(bot_token="x")
    br._perf_enabled = False
    br._perf = {}
    br._on_agent_status = lambda sid: (res, 1.0)
    return br._heartbeat_status_line("s1")


def push_text(events):
    """The text the phone's notification is made from when a turn ends (api_push._push_turn_end), on a transcript."""
    cues = []
    p = type("P", (api_push.PushApiMixin,), {})()
    p.sessions = {"s1": Tab("s1", "示範分頁")}
    p._worker_ctx = lambda sid, s: {}
    p._push_cue = lambda kind, sid, text="", **kw: cues.append((kind, text))
    keep = ag.resolve_transcript, ag._read_tail_events
    ag.resolve_transcript = lambda worker: "/nowhere"
    ag._read_tail_events = lambda path: ("claude", events, "")
    try:
        p._push_turn_end("s1", status_of(events, "done"))
    finally:
        ag.resolve_transcript, ag._read_tail_events = keep
    return cues[-1] if cues else ("", "")


def notify_line(res):
    return sf_notify.AutoNotifier(loader=lambda: None, sender=lambda p, c: (True, ""), log=lambda *a: None)._message("DONE", res)


def user(text):
    return {"kind": "user_msg", "text": text}


def bot(text):
    return {"kind": "assistant_text", "text": text}


print("the transcript, as Claude Code writes it: _read_tail_events → _detail")
rec = lambda role, content, i: json.dumps({"type": role, "sessionId": "x", "timestamp": f"2026-11-12T10:{i:02d}:00Z",
                                           "message": {"role": role, "content": content}}, ensure_ascii=False)
A2A_SHORT = ps.SHAPES[0][1]
path = TMP / "t.jsonl"
path.write_text("\n".join([
    rec("user", A2A_SHORT, 0),
    rec("assistant", [{"type": "text", "text": "[[TG_REPLY_ab12cd34]]\n收到，我先看比較表。\n[[/TG_REPLY_ab12cd34]]"}], 1),
    rec("user", [{"type": "text", "text": ps.SHAPES[5][1]}], 2),
    rec("assistant", [{"type": "text", "text": "[[SF:WORKING]]\n好的，開始整理週報。"}], 3),
]) + "\n", encoding="utf-8")
fmt, evs, err = ag._read_tail_events(str(path))
action, task, narration = ag._detail(evs)
check("a transcript file of the real shape is read", fmt == "claude" and len(evs) == 4 and not err, (fmt, len(evs), err))
check("task: the person's last message on Telegram, wrapped whole as one paste: their words",
      task == "從手機傳的，第 1 行。 從手機傳的，第 2 行。 從手機傳的，第 3 行。 從手機傳的，第 4 行。 從手機傳的，第 5 行。", task)
check("narration: the agent's last words without its [[SF:WORKING]]", narration == "好的，開始整理週報。", narration)
_, task2, narration2 = ag._detail(evs[:2])
check("an A2A message as a list-less string: who sent it, then the words", task2 == "研究：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。 比較表整理好了，第 3 行。", task2)
check("a Telegram reply: what it said, not its marker", narration2 == "收到，我先看比較表。", narration2)

print("what _detail makes of every shape")
for name, text, frm, view in ps.SHAPES:
    t = ag._detail([user(text)])[1]
    check(f"{name}: a plain line of at most 90", clean(t) and 0 < len(t) <= 90, t)
    if frm and frm.get("kind") == "a2a":
        check(f"{name}: leads with {frm['name']}", t.startswith(frm["name"]), t)
check("a message that says nothing (only the Telegram instruction) is no task: an older one shows",
      ag._detail([user("週報做好了嗎"), bot("好了"), user(ps.TG_ASK)])[1] == "週報做好了嗎")
check("a reply that is only a marker is no narration: the one before it shows",
      ag._detail([bot("先前的回覆"), user("再來"), bot("[[SF:GREEN]]")])[2] == "先前的回覆")
check("a harness record (Codex's <environment_context>) is no task: the message before it shows",
      ag._detail([user("請修 狀態偵測"), user("<environment_context>\n  <cwd>/repo</cwd>\n</environment_context>")])[1] == "請修 狀態偵測")
check("a system reminder around the words is not part of them",
      ag._detail([user("<system-reminder>\n今天的日期\n</system-reminder>\n幫我看一下這個 bug")])[1] == "幫我看一下這個 bug")

print("the phone and the lists")
CASES = [(name, text, want) for (name, text, _f, _v), want in zip(ps.SHAPES, [
    "研究：比較表整理好了", "寫程式：測試都跑完了", "排程：週報草稿", "貼上的內容 · 15 行", "我自己打的一段長訊息",
    "從手機傳的", "排程（未轉達）：比較表整理好了"])]
CASES += [("an A2A hand-off, not wrapped", agent_link.format_handoff("研究", "把比較表整理成三點", "h1", lang="zh"), "研究：把比較表整理成三點"),
          ("a short message from Telegram, not wrapped", ps.TG_ASK + "\n\n週報做好了嗎", "週報做好了嗎")]
for name, text, want in CASES:
    res = status_of([user(text)], "done")
    api = make_api(res)
    latest = api._execute_sfctl("bots_link", {})["tabs"]["s1"]["latest"]
    act = api._agent_activity_for_list("s1")
    hb = heartbeat_line(res)
    hook = notify_line(res)
    for where, line in (("GET /link/bots latest", latest), ("agent_activity", act), ("Telegram heartbeat", hb), ("webhook", hook)):
        check(f"{name}: {where} says {want[:16]!r}… and nothing of the wrapper", clean(line) and want in line, line)
check("the phone's line is at most 140 characters, the list's 120", len(make_api(status_of([user(ps.SHAPES[2][1])], "done"))
      ._execute_sfctl("bots_link", {})["tabs"]["s1"]["latest"]) <= 140 and len(make_api(status_of([user(ps.SHAPES[2][1])], "done"))._agent_activity_for_list("s1")) <= 120)

print("the agent's last words: the phone, sfctl bots, the heartbeat")
TG_REPLY = "[[TG_REPLY_ab12cd34]]\n週報整理好了，放在 reports/weekly.md\n[[/TG_REPLY_ab12cd34]]"
for name, text, want in (("a Telegram reply", TG_REPLY, "週報整理好了，放在 reports/weekly.md"),
                         ("a reply with the marker on its own line first", "[[SF:WORKING]]\n開始處理週報", "開始處理週報"),
                         ("a reply with its markers inline", "[[TG_REPLY_ab12cd34]]好了，放在 reports/weekly.md[[/TG_REPLY_ab12cd34]]", "好了，放在 reports/weekly.md")):
    res = status_of([user("幫我整理週報"), bot(text)], "done")
    api = make_api(res)
    latest = api._execute_sfctl("bots_link", {})["tabs"]["s1"]["latest"]
    check(f"{name}: GET /link/bots latest", clean(latest) and latest == want, latest)
    check(f"{name}: sfctl bots", api._bots_live("s1")[1] == want, api._bots_live("s1"))
    check(f"{name}: the heartbeat's status line", clean(heartbeat_line(res)) and want in heartbeat_line(res), heartbeat_line(res))

print("a push notification's body: the last line of the turn, not the Telegram marker that closes it")
check("last_words on a turn that ended with [[/TG_REPLY_…]] on a line of its own",
      api_push.last_words([bot(TG_REPLY)]) == "週報整理好了，放在 reports/weekly.md", api_push.last_words([bot(TG_REPLY)]))
check("…and with the marker inline", api_push.last_words([bot("好了，放在 reports/weekly.md[[/TG_REPLY_ab12cd34]]")]) == "好了，放在 reports/weekly.md")
check("…[[SF:…]] lines still go", api_push.last_words([bot("做完了\n[[SF:GREEN]]\n[[SF:MEMORY:SAVED]]")]) == "做完了")
check("…a reply that is only markers has no last words", api_push.last_words([bot("[[TG_REPLY_ab12cd34]]\n[[/TG_REPLY_ab12cd34]]")]) == "")
kind, text = push_text([user("幫我整理週報"), bot(TG_REPLY)])
check("the cue _push_turn_end sends is a done cue with those words", kind == "done" and text == "週報整理好了，放在 reports/weekly.md", (kind, text))
kind, text = push_text([user(A2A_SHORT), bot("[[SF:GREEN]]")])
check("a turn with no words of its own falls back to the task, plain: who asked, and what", kind == "done" and text.startswith("研究：比較表整理好了") and clean(text), (kind, text))

print("a [[SF:RED]] question in a Telegram reply: the question and its options, on the phone and for the buttons")
RED_WANT = ("要先修哪個？", [("1", "先修按鈕"), ("2", "先修通知")])
RED_CASES = {
    "the start marker on the question's own line": "[[TG_REPLY_ab12cd34]]要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知\n[[/TG_REPLY_ab12cd34]]",
    "the end marker on the last option's line": "要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知[[/TG_REPLY_ab12cd34]]",
    "markers on lines of their own": "[[TG_REPLY_ab12cd34]]\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知\n[[/TG_REPLY_ab12cd34]]",
    "a marker-only line right above [[SF:RED]]": "要先修哪個？\n[[TG_REPLY_ab12cd34]]\n[[SF:RED]]\n1. 先修按鈕\n2. 先修通知",
}
for name, text in RED_CASES.items():
    evs = [user("幫我看一下"), bot(text)]
    check(f"{name}: red_block's question and options", bridge_home.red_block(evs, telegram_too=True) == RED_WANT, bridge_home.red_block(evs, telegram_too=True))
    api = make_api(status_of(evs, "done"))
    bridge_home.on_turn_end(api, "s1", read_events=lambda sid, e=evs: e)
    d = api._execute_sfctl("bots_link", {})["tabs"]["s1"].get("decision") or {}
    bridge_home.forget("s1")
    check(f"{name}: the phone's decision", d.get("kind") == "red" and d.get("reason") == RED_WANT[0]
          and d.get("options") == [{"n": 1, "text": "先修按鈕"}, {"n": 2, "text": "先修通知"}] and clean(json.dumps(d, ensure_ascii=False)), d)
check("a question with no markers is as it was (the sentence above the marker, not a list item)",
      bridge_home.red_block([user("x"), bot("兩個方案都可以。\n- 方案甲\n要先修哪個？\n[[SF:RED]]\n1. 先修按鈕")], telegram_too=True) == ("要先修哪個？", [("1", "先修按鈕")]))

print("Telegram /list: a screen as Claude Code draws it, through the real _peek_last_response")
RULE = "─" * 40
EMPTY_SCREEN = types.SimpleNamespace(history=types.SimpleNamespace(top=[]), columns=80, display=[])   # pyte, when tmux has nothing
SCREENS = {
    "the agent answered a Telegram message": (
        "\n".join(["❯ " + ps.TG_ASK, "", "  週報做好了嗎", "", "⏺ [[TG_REPLY_ab12cd34]]", "  週報整理好了，放在 reports/weekly.md",
                   "  [[/TG_REPLY_ab12cd34]]", "", RULE, "❯ ", RULE, "  ? for shortcuts"]),
        "   週報整理好了，放在 reports/weekly.md"),
    "another agent just sent a message and nothing has answered it yet": (
        "\n".join(["❯ " + agent_link.format_delivery("研究", "比較表整理好了，第 1 行。", lang="zh").split("\n")[0],
                   "  比較表整理好了，第 1 行。", "  比較表整理好了，第 2 行。", "", "✻ Thinking…", "", RULE, "❯ ", RULE]),
        "   研究：比較表整理好了，第 1 行。 比較表整理好了，第 2 行。"),
    "a plain answer, four lines": (
        "\n".join(["❯ 幫我整理週報", "", "⏺ 週報整理好了，放在 reports/weekly.md", "  第二行", "  第三行", "  第四行", "", RULE, "❯ ", RULE]),
        "   週報整理好了，放在 reports/weekly.md 第二行 第三行"),
    "an empty tab": ("", "   (no recent activity)"),
}
for name, (screen, want) in SCREENS.items():
    br = object.__new__(bt.TelegramBridge)
    br._tmux_capture = lambda sid, s=screen: s
    slot = types.SimpleNamespace(sid="s1", peek_fn=None, index=1, label="示範分頁", screen=EMPTY_SCREEN)
    got = bridge_home.list_snippet(br._peek_last_response(slot))
    check(f"{name}: {want.strip()!r}", got == want and clean(got), got)
check("one line, at most 220 characters and a 3-space indent", "\n" not in bridge_home.list_snippet("字\n" * 500)
      and len(bridge_home.list_snippet("字" * 900)) == 223 and bridge_home.list_snippet("字" * 900).startswith("   字"))

print("…and the whole /list message")
for name, screen_want in (("the answer", SCREENS["the agent answered a Telegram message"]), ("a message just sent", SCREENS["another agent just sent a message and nothing has answered it yet"])):
    screen, want = screen_want
    br = object.__new__(bt.TelegramBridge)
    br._slots_lock = threading.Lock()
    br._tmux_capture = lambda sid, s=screen: s
    slot = types.SimpleNamespace(sid="s1", peek_fn=None, index=1, label="示範分頁", screen=EMPTY_SCREEN)
    br.slots, br._slot_order, br._user_active = {"s1": slot}, ["s1"], {}
    br._last_prune_ts = time.time()
    br._prune_stale_slots = lambda force=False: None
    br.paused, br.bot_info = False, {"username": "demo_bot"}
    br.config = types.SimpleNamespace(bot_token="TEST", allowed_users=[])
    br._on_model_info = lambda sid: None
    sent = []
    bt.tg_api = lambda tok, m, p, timeout=35: sent.append((m, p)) or {}
    br._handle_command("list", 111, 222)
    text = next((p["text"] for m, p in sent if m == "sendMessage"), "")
    check(f"{name}: the tab's row and one plain line under it", "/1  示範分頁" in text and want in text.split("\n") and clean(text), text)

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
