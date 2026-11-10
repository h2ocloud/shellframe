#!/usr/bin/env python3
"""Text for a tab waits for the tab: never into a running turn, a decision menu or
the user's half-typed words. ShellBot does the waiting, so it holds for any model.

Measured before: `sfctl send`, A2A and delegate typed into the tab at once while
the docs said `send` waited; an agent promising "I'll hand it over when it's done"
had to poll by itself. And a dim suggestion Claude Code paints in the input box
read like the user's own words once the colours were stripped.

  - the input box is read with its colours: dim (SGR 2) = Claude Code's suggestion,
    plain = the user's draft (both shapes captured from live tabs)
  - a working / deciding / Telegram-busy / drafting tab gets the text queued
  - one tab's queue is first in, first out; the next waits until the last is taken
  - --now goes in at once; a timeout or a closed tab tells the sender
  - sfctl send, A2A and delegate to an open tab all go through the queue

Run: .venv/bin/python tests_tab_delivery.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-deliver-"))
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(Path(tempfile.mkdtemp(prefix="sf-log-")) / "shellframe_debug.log")  # fake tabs stay out of the real log

import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import a2a_host  # noqa: E402
import main  # noqa: E402
import tab_delivery as td  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_a2a": True}, "agent_roster": {
    "coder": {"label": "coder", "cmd": "claude", "responsibility": "code"}}}), encoding="utf-8")
td.POLL, td.TAKEN_WAIT = 0.05, 0.4

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


RULE = "─" * 40
box = lambda *rows: "\n".join(["some conversation", RULE, *rows, RULE, "  footer"])

print("the input box")
check("empty prompt", td.composer_state(box("\x1b[39m❯\xa0")) == ("empty", ""))
check("Claude Code's dim suggestion is a suggestion (live capture)",
      td.composer_state(box("\x1b[39m❯\xa0\x1b[2m好 繼續追 等第 3 項做完再回報\x1b[0m"))
      == ("suggestion", "好 繼續追 等第 3 項做完再回報"))
check("plain text is the user's draft", td.composer_state(box("\x1b[39m❯\xa0寫到一半的訊息"))[0] == "draft")
check("typing over a suggestion is a draft", td.composer_state(box("\x1b[39m❯\xa0abc\x1b[2mdef\x1b[0m"))[0] == "draft")
check("a paste chip is the user's", td.composer_state(box("\x1b[39m❯\xa0[Pasted text #1 +3 lines]"))[0] == "draft")
check("a multi-line draft", td.composer_state(box("\x1b[39m❯\xa0line one", "line two"))
      == ("draft", "line one\nline two"))
check("combined SGR 2;37 is dim, 22 ends it", td.composer_state(box("❯ \x1b[2;37mhint\x1b[22m"))[0] == "suggestion"
      and td.composer_state(box("❯ \x1b[2mhint\x1b[22mtyped"))[0] == "draft")
# A pane without focus: Claude Code draws its own cursor, an inverse block over the first
# character of the suggestion (as captured from a real tab).
check("the cursor over a suggestion's first character is the suggestion's",
      td.composer_state(box('\x1b[39m❯\xa0\x1b[7mT\x1b[0;2mry\x1b[0m \x1b[2m"how\x1b[0m \x1b[2mdoes\x1b[0m'))[0] == "suggestion")
check("…but typed text with the cursor after it stays a draft",
      td.composer_state(box("\x1b[39m❯\xa0hello\x1b[7m \x1b[0m"))[0] == "draft")
check("…one typed character under the cursor is a draft",
      td.composer_state(box("\x1b[39m❯\xa0\x1b[7ma\x1b[0m"))[0] == "draft")
check("…and so is typed text with the cursor on one of its characters",
      td.composer_state(box("\x1b[39m❯\xa0he\x1b[7ml\x1b[27mlo"))[0] == "draft")
check("SGR 27 ends inverse, 0 ends both", [x[2] for x in td._chars_with_dim("\x1b[7ma\x1b[27mb\x1b[7;2mc\x1b[0md")]
      == [True, False, True, False])
check("no input box in view: unknown, assume nothing", td.composer_state("› Ask Codex\n  ready")[0] == "unknown")
check("peek's note names a suggestion as not the user's",
      "not the user's" in td.composer_note("suggestion", "hint") and td.composer_note("empty", "") == "")


class Tab:
    def __init__(self, sid, label):
        self.sid, self._custom_label, self.cmd = sid, label, "claude"
        self.alive, self.lock, self._recent = True, threading.Lock(), bytearray()
        self._tmux_name = None
        self._startup_trust_pending = True
        self._last_output_activity_time = time.time()


def make_api(*sids):
    api = Api.__new__(Api)
    api.sessions = {sid: Tab(sid, label) for sid, label in sids}
    api.bridge = api.line_bridge = None
    api._window = None
    api.state = {sid: "" for sid, _ in sids}
    api.screen = {sid: "" for sid, _ in sids}
    api.sent = []
    api.prompt_at = {}

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
        api.prompt_at[s.sid] = time.time()
    api._send_text_to_session = send
    api._agent_state_for_list = lambda sid: api.state.get(sid, "")
    api._agent_status_snapshot = lambda sid: ({"prompt_at": api.prompt_at.get(sid, 0)}, 0)
    return api


real_capture = td.capture
td.capture = lambda api, sid: api.screen.get(sid, "")

print("waiting")
api = make_api(("s1", "coder"), ("s2", "boss"))
check("free tab: in at once", td.deliver(api, "s1", "hi")["queued"] is False and api.sent == [("s1", "hi")])
for st, why in (("working", "working"), ("decision", "decision")):
    api.state["s1"] = st
    check(f"a tab that is {st} waits", td.busy_reason(api, "s1") == why)
api.state["s1"] = ""
api._bridge_session_busy = lambda sid: True
check("a Telegram turn in flight waits", td.busy_reason(api, "s1") == "telegram")
api._bridge_session_busy = lambda sid: False
api.screen["s1"] = box("\x1b[39m❯\xa0half typed")
check("the user's draft waits", td.busy_reason(api, "s1") == "draft")
api.screen["s1"] = box("\x1b[39m❯\xa0\x1b[2ma suggestion\x1b[0m")
check("a suggestion does not", td.busy_reason(api, "s1") == "")
# Measured: after a restart a running tab read "idle 0s" for 8 s, until the status monitor's
# first look; a message sent then went straight into its turn.
api.screen["s1"] = "\n".join(["x", "✳ Undulating… (29m 32s · ↓ 158.0k tokens)", "", RULE, "\x1b[39m❯\xa0", RULE, "  footer"])
check("no state yet (just restarted): a turn on screen waits", td.busy_reason(api, "s1") == "working")
api.state["s1"] = "done"
check("…but once there is a state it is the one that counts", td.busy_reason(api, "s1") == "")
api.state["s1"] = ""
api.screen["s1"] = ""

print("the queue")
api.sent.clear()
api.state["s1"] = "working"
r1 = td.deliver(api, "s1", "first")
r2 = td.deliver(api, "s1", "second")
check("busy: queued, in order", r1["queued"] and r1["position"] == 1 and r2["position"] == 2 and not api.sent,
      (r1, r2))
check("…and the reply says how to skip the line", "--now" in r1["message"])
check("pending() shows what waits", [p["chars"] for p in td.pending("s1")] == [5, 6])
api.state["s1"] = ""
check("when the turn ends, the first goes in", wait_for(lambda: api.sent[:1] == [("s1", "first")]))
check("…then the second, after the first was taken", wait_for(lambda: api.sent == [("s1", "first"), ("s1", "second")]))
check("nothing left waiting", wait_for(lambda: not td.pending("s1")))

api.sent.clear()
api.state["s1"] = "working"
check("--now goes in mid-turn", td.deliver(api, "s1", "urgent", now=True)["queued"] is False
      and api.sent == [("s1", "urgent")])

print("timeouts")
api.sent.clear()
td.QUEUE_MAX_WAIT = 0.3
told = []
td.deliver(api, "s1", "never", from_sid="s2", on_timeout=lambda r, why: told.append((r, why)))
check("a timeout tells the sender why", wait_for(lambda: told == [("working", td._reason_text("working"))]), told)
check("…and the text never went in", ("s1", "never") not in api.sent)
td.notify_sender(api, "s2", "coder", "it is in the middle of a turn")
check("the sender's tab gets a note", wait_for(lambda: any(s == "s2" and "not delivered" in t for s, t in api.sent)))
told.clear()
td.QUEUE_MAX_WAIT = 30
td.deliver(api, "s1", "orphan", on_timeout=lambda r, why: told.append(r))
api.sessions.pop("s1")
check("a tab closed while its text waits: the sender hears it", wait_for(lambda: told == ["closed"]), told)
td.QUEUE_MAX_WAIT = 1800.0

print("every way in")
api = make_api(("s1", "coder"), ("s2", "boss"))
api.state["s1"] = "working"
res = api._execute_sfctl("send", {"sid": "s1", "text": "from sfctl", "from_sid": "s2"})
check("sfctl send to a busy tab is queued", res["success"] and (res.get("details") or {}).get("queued"), res)
api.state["s1"] = ""
check("…and goes in when it frees", wait_for(lambda: ("s1", "from sfctl") in api.sent))
api.state["s1"] = "working"
res = api._execute_sfctl("send", {"sid": "s1", "text": "now please", "now": True})
check("sfctl send --now goes in at once", ("s1", "now please") in api.sent and not (res.get("details") or {}).get("queued"))

api.sent.clear()
api.state["s1"] = "working"
res = a2a_host.handle(api, "a2a_send", {"target": "coder", "text": "a2a hello", "from_sid": "s2"})
check("A2A to a busy tab is queued", res.get("success") and (res.get("details") or {}).get("queued"), res)
check("…and logged as queued", any(e.get("kind") == "queued" for e in agent_link.history(20)))
api.state["s1"] = ""
check("…and delivered when the turn ends", wait_for(lambda: any(s == "s1" and "a2a hello" in t for s, t in api.sent)))

api.sent.clear()
api.state["s1"] = "working"
api._wait_until_ready = lambda sid, timeout=45.0: True
res = api.delegate_task("coder", "please refactor")
check("delegate to an open busy tab is queued, not typed in", res["success"] and "Queued" in res["message"]
      and not any("please refactor" in t for _, t in api.sent), res)
api.state["s1"] = ""
check("…and goes in when it frees", wait_for(lambda: any(s == "s1" and "please refactor" in t for s, t in api.sent)))

print("--ttl")
check("no --ttl: 30 min; 0: until free (a day at most); 90: 90 min; huge: a day; negative: default",
      (td.wait_limit(None), td.wait_limit(0), td.wait_limit(90), td.wait_limit(99999), td.wait_limit(-5))
      == (1800.0, 86400.0, 5400.0, 86400.0, 1800.0))
check("a2a-result's default is a day", td.wait_limit(None, td.RESULT_WAIT) == 86400.0)
api = make_api(("s1", "coder"), ("s2", "boss"))
api.state["s1"] = "working"
told = []
r = td.deliver(api, "s1", "short", on_timeout=lambda o, why: told.append("short"), max_wait=0.3)
td.deliver(api, "s1", "long", on_timeout=lambda o, why: told.append("long"))
check("each item keeps its own limit", "up to 0 min" in r["message"] and "--ttl" in r["message"], r["message"])
check("…the short one times out on its own limit, the next keeps waiting",
      wait_for(lambda: told == ["short"], 3) and td.pending("s1") and td.pending("s1")[0]["chars"] == 4, (told, td.pending("s1")))
td.LATE_NOTE_S = 0.2
time.sleep(0.3)
api.state["s1"] = ""
check("an item that waited past the note's limit goes in with a line saying how long",
      wait_for(lambda: any(s == "s1" and t.splitlines()[0].startswith("[[ShellBot: this waited")
                           and t.splitlines()[-1] == "long" for s, t in api.sent)), api.sent)
td.LATE_NOTE_S = 1800.0
api.sent.clear()
api.state["s1"] = "working"
res = api._execute_sfctl("send", {"sid": "s1", "text": "via sfctl", "ttl": 90, "from_sid": "s2"})
check("sfctl send --ttl 90 queues it for 90 minutes", res["success"] and td._queues["s1"][-1].get("max_wait") == 5400.0,
      res)
res = a2a_host.handle(api, "a2a_send", {"target": "coder", "text": "a2a with ttl", "from_sid": "s2", "ttl": 0})
check("a2a-send --ttl 0: until the tab is free (a day at most)", td._queues["s1"][-1].get("max_wait") == 86400.0, res)
api.state["s1"] = ""
wait_for(lambda: not td.pending("s1"))
h = a2a_host.handle(api, "a2a_handoff", {"target": "coder", "text": "do the thing", "from_sid": "s2"})
hid = (h.get("details") or {}).get("id")
api.state["s2"] = "working"
res = a2a_host.handle(api, "a2a_result", {"id": hid, "text": "done it", "from_sid": "s1"})
check("a2a-result to a busy requester waits a day by default", res.get("success")
      and td._queues["s2"][-1].get("max_wait") == 86400.0, (res, td.pending("s2")))
check("the limit is kept on disk with the item", "max_wait" in td._KEEP)
api.state["s2"] = ""
wait_for(lambda: not td.pending("s2"))

print("is a turn running (no hook yet, e.g. right after a restart)")
# Measured: for 80 s after a restart s347 read done while its screen said
# "Undulating… (12m 53s)". Newer Claude Code dropped "esc to interrupt" from that line.
import agent_status as ag  # noqa: E402
SCREENS = {
    "· Dilly-dallying… (21s · ↓ 1.7k tokens)": True,
    "✻ Undulating… (12m 53s)": True,
    "✶ Pondering… (1h 2m 3s · ↓ 9k tokens)": True,
    "✻ Thinking… (esc to interrupt)": True,
    "✳ Undulating… (29m 32s · ↓ 158.0k tokens)                ": True,
    "* Thinking… (4s)": True,                     # the glyph Claude Code uses on other platforms
    "✻ Baked for 1s · done 3:34 PM": False,
    "Loading the list (5s)": False,
    # The same words quoted in a conversation are not a running turn: an idle tab that showed
    # one would read working (and hold every message for it) until its next hook event.
    "  - Coding tab: Undulating… (25m 9s)": False,              # a reply quoting another tab
    "     · Undulating… (25m 9s · ↓ 131.1k tokens)": False,     # tool output, e.g. peek --raw
    "❯ Why does it say Undulating… (12m 53s": False,           # the user's own prompt
    "⏺ Undulating… (3s) is the spinner's verb": False,
    "- Undulating… (3s)": False,
}
for line, want in SCREENS.items():
    scr = "\n".join(["some conversation", line, RULE, "\x1b[39m❯\xa0", RULE, "  footer"])
    check(f"{'running' if want else 'not running'}: {line[:30]}", bool(td.status_line(scr)) is want, td.status_line(scr))
    check(f"…the status tracker agrees", ag._screen_signals(scr)["actively_working"] is want)
check("a short conversation (blank rows under the input box) still shows its status line",
      td.status_line("\n".join(["✳ Undulating… (3s)", RULE, "❯ ", RULE] + [""] * 20)) == "✳ Undulating… (3s)")
check("compute_state from the screen alone: working",
      ag.compute_state([], now=0, screen_tail="✻ Undulating… (12m 53s)\n" + RULE + "\n❯ \n" + RULE)[0] == "working")

print("peek shows whether it is running")
api = make_api(("s1", "coder"))
api.get_clean_history = lambda sid, max_lines=200: json.dumps({"success": True, "text": "user: go\nassistant: on it"})
api.screen["s1"] = "\n".join(["x", "· Dilly-dallying… (21s · ↓ 1.7k tokens)", RULE, "\x1b[39m❯\xa0", RULE])
res = api._execute_sfctl("peek", {"sid": "s1"})
text = (res.get("details") or {}).get("text", "")
check("the cleaned conversation ends with the status line", text.splitlines()[-1].endswith("Dilly-dallying… (21s · ↓ 1.7k tokens)")
      and "assistant: on it" in text and res["details"].get("running"), text)
api.sessions["s1"]._recent = bytearray("old line\n\x1b[2m· Pondering… (3s)\x1b[0m\n❯ ".encode())
res = api._execute_sfctl("peek", {"sid": "s1", "raw": True})
check("--raw: the screen itself, spinner and input box included, without colour codes",
      "· Pondering… (3s)" in res["details"]["text"] and "\x1b" not in res["details"]["text"], res["details"]["text"])
api.screen["s1"] = ""
res = api._execute_sfctl("peek", {"sid": "s1"})
check("nothing running: no status line", "[ShellBot]" not in res["details"]["text"] and not res["details"].get("running"))

print("a new conversation is ready")
# Measured live: a delegate to a role with no open tab opened it, then waited 45 s
# for an empty `❯` line that a new Claude Code conversation never shows.
api_r = make_api(("s9", "fresh"))
api_r._auto_accept_startup_trust_prompt = lambda sid, s: None
api_r.screen["s9"] = "\n".join(["", "", RULE, "\x1b[39m❯\xa0\x1b[2mTry \"how does <filepath> work?\"\x1b[0m",
                                RULE, "  ⏵⏵ bypass permissions on"])
check("a new Claude Code conversation, its dim placeholder in the box, is ready",
      api_r.is_session_ready_for_bridge("s9"))
api_r.screen["s9"] = box("\x1b[39m❯\xa0half typed")
check("…not when the box holds the user's draft", not api_r.is_session_ready_for_bridge("s9"))
api_r.screen["s9"] = "starting claude…"
check("…nor before the box is drawn", not api_r.is_session_ready_for_bridge("s9"))

td.capture = real_capture
check("capture reads nothing for a tab without tmux", td.capture(api, "s1") == "")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
