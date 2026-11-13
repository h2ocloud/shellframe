#!/usr/bin/env python3
"""An open bot saves its memory after real work and a quiet spell (bot_memory.py).

It is asked (one turn of its own) when there was real conversation since the last save and its tab has
been idle for settings.bot_memory_idle_min (45 min); and never when
  - the setting is off, the tab is not a memory bot's (a temporary bot, an outside bot, memory off) or runs a
    CLI whose conversation ShellBot cannot read
  - the tab is not idle yet, or nothing happened since the last save, or what happened is only ShellBot's
    own wake-up brief and the bot's "ready" (or a "hi" and its answer)
  - a turn is running, a menu waits for the person, the bot's own [[SF:RED]] question waits (until the person
    has answered it), the input box holds the person's draft, texts are queued for the tab
  - the tab is closing, or the last request is still being written
It is sent through tab_delivery without queueing, does not count as the person typing (the idle reaper's clock),
and the next save of the same conversation rewrites the same summary file.

Demo data only. Run: .venv/bin/python tests_bot_memory.py
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
TMP = Path(tempfile.mkdtemp(prefix="sf-bot-memory-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "en"
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
LOG = TMP / "shellframe_debug.log"
sf_log.DEBUG_LOG = str(LOG)
import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)
import api_bot_memory  # noqa: E402
import bot_memory as bm  # noqa: E402
import bots  # noqa: E402
import bridge_home  # noqa: E402
import main  # noqa: E402
import tab_delivery as td  # noqa: E402
from main import Api  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


main.CONFIG_FILE = TMP / "config.json"
bm.POLL, bm.TAKE_WAIT, bm.ATTEMPT_WAIT = 0.03, 0.5, 3.0
td.POLL = 0.03
RULE = "─" * 40
box = lambda *rows: "\n".join(["some conversation", RULE, *rows, RULE, "  footer"])
NOW = time.time()
LONG_AGO = NOW - 3 * 3600


def wait_for(cond, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class Tab:
    def __init__(self, sid, label, cmd="claude"):
        self.sid, self._custom_label, self.cmd, self.alive = sid, label, cmd, True
        self.lock, self._recent, self._tmux_name = threading.Lock(), bytearray(), None
        self._startup_trust_pending = True
        self._last_activity_time = self._last_user_activity_time = self._last_output_activity_time = LONG_AGO


ROSTER = {"alpha": {"label": "alpha", "cmd": "claude", "bot": {"memory": True}},
          "nomem": {"label": "nomem", "cmd": "claude", "bot": {"memory": False}},
          "outside": {"label": "outside", "cmd": "zsh", "bot": {"external": {"kind": "webhook", "url": "https://example.com/h"}}},
          "plain": {"label": "plain", "cmd": "claude"}}
SESSIONS = {"s1": {"bot": "alpha"}, "s2": {"bot": "nomem"}, "s3": {"bot": "plain"}}


def write_config(settings=None):
    st = {"experimental_bots": True}
    st.update(settings or {})
    main.CONFIG_FILE.write_text(json.dumps({"settings": st, "agent_roster": ROSTER, "bots_sessions": SESSIONS}), encoding="utf-8")


def cfg():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


u = lambda text, ts: {"kind": "user_msg", "text": text, "ts": ts}
a = lambda ts, text="done": {"kind": "assistant_text", "text": text, "ts": ts}
tool = lambda ts: {"kind": "tool_call", "tool": "Bash", "target": "ls", "ts": ts}
WORK = [u("please tidy the release checklist", LONG_AGO + 10), tool(LONG_AGO + 20), a(LONG_AGO + 30, "tidied")]


def make_api(*tabs):
    api = Api.__new__(Api)
    api.sessions = {sid: Tab(sid, label, cmd) for sid, label, cmd in tabs}
    api.bridge = api.line_bridge = None
    api._window = None
    api.state = {sid: "" for sid, _, _ in tabs}
    api.screen = {sid: "" for sid, _, _ in tabs}
    api.turns = {sid: list(WORK) for sid, _, _ in tabs}
    api.activity = {sid: LONG_AGO + 30 for sid, _, _ in tabs}
    api.sent = []
    api.prompt_at = {}
    api.bot_writes = True           # the bot does what it is asked

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
        s._last_activity_time = s._last_user_activity_time = time.time()     # what the real one does
        api.prompt_at[s.sid] = time.time()
        api.state[s.sid] = "working"

        def bot():
            time.sleep(0.15)
            if api.bot_writes:
                import re
                m = re.search(r"to (\S+\.md) \(", text)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_text("# summary\n- tidied the checklist\n", encoding="utf-8")
                mm = re.search(r"memory (\S+MEMORY\.md) \(", text)
                if mm:
                    Path(mm.group(1)).write_text("- the person likes short notes\n", encoding="utf-8")
            api.state[s.sid] = "done"
        threading.Thread(target=bot, daemon=True).start()
        return True
    api._send_text_to_session = send
    api._agent_state_for_list = lambda sid: api.state.get(sid, "")
    api._agent_status_snapshot = lambda sid: ({"prompt_at": api.prompt_at.get(sid, 0)}, 0)
    api._bridge_session_busy = lambda sid: api.telegram_busy.get(sid, False)
    api.telegram_busy = {}
    api._last_activity_at = lambda sid, s, out_ts=0.0: api.activity.get(sid, 0)
    api._execute_sfctl = lambda cmd, args: {"success": True, "details": {"turns": api.turns.get(args["sid"], [])}} \
        if cmd == "conversation" else {"success": False}
    return api


td.capture = lambda api, sid: api.screen.get(sid, "")


def reset():
    """Fresh bot folders, no state left from an earlier case, the demo config."""
    import shutil
    shutil.rmtree(TMP / "bots", ignore_errors=True)
    bm._flight.clear()
    bm._state.clear()
    bridge_home._red.clear()
    td._queues.clear()
    write_config()
    return make_api(("s1", "alpha", "claude"), ("s2", "nomem", "claude"), ("s3", "plain", "claude"))


def why(api, sid="s1", mins=45, now=NOW):
    s = api.sessions[sid]
    role = api._bots_role_of(sid, cfg())
    return bm.why_not(api, sid, s, role, ROSTER[role], mins, now)


print("1. settings")
check("on by default, 45 minutes", bm.settings({}) == (True, 45) and bm.settings({"settings": {}}) == (True, 45))
check("it can be turned off and the idle time changed", bm.settings({"settings": {"bot_memory_autosave": False, "bot_memory_idle_min": 90}}) == (False, 90))
check("the idle time is kept between 5 minutes and a day; nonsense is the default",
      bm.settings({"settings": {"bot_memory_idle_min": 1}})[1] == 5 and bm.settings({"settings": {"bot_memory_idle_min": 99999}})[1] == 1440
      and bm.settings({"settings": {"bot_memory_idle_min": "soon"}})[1] == 45 and bm.settings({"settings": {"bot_memory_idle_min": "30"}})[1] == 30)

print("2. the request")
api = reset()
mine = bm.prompt("alpha", ROSTER["alpha"], "/x/sessions/a.md", "/x/MEMORY.md", lang="en")
check("it starts with the line the chat view folds the round on, then plain words", mine.splitlines()[0] == bm.MARK
      and mine.splitlines()[1].startswith("ShellBot asks you to save your memory now"), mine)
check("it names the summary file, MEMORY.md, the size it must keep and the saved marker",
      "/x/sessions/a.md" in mine and "/x/MEMORY.md" in mine and str(bots.MEMORY_CAP) in mine and bots.SAVED_MARK in mine)
check("what came from outside is not stored: said as at close (other bots, webhooks, Telegram)",
      "other bots, webhooks or Telegram" in mine and "never store it" in mine and "do nothing else besides" in mine)
check("it says this is not the end, and not to report it to the user", "not the end" in mine and "not from the user" in mine)
zh = bm.prompt("alpha", ROSTER["alpha"], "/x/sessions/a.md", "/x/MEMORY.md", lang="zh")
check("in Chinese too, with the same line first", zh.splitlines()[0] == bm.MARK and "其他 bot、webhook、Telegram" in zh and "不是使用者" in zh)
check("the CLI's paste wrapper does not hide it", bm.is_request(f'<pasted_content id="1">\n{mine}\n</pasted_content id="1">') and bm.is_request(mine)
      and not bm.is_request("a message that mentions [[ShellBot: memory autosave]] later"))

print("3. which turns are real conversation")
brief = bots.compose_brief("alpha", ROSTER["alpha"], ROSTER, lang="en")
check("ShellBot's own messages are not conversation: the wake-up brief (both languages), the close request, the save request, its notes",
      all(bm.classify(t)[0] == "own" and not bm.real_text(t) for t in (
          brief, bots.compose_brief("alpha", ROSTER["alpha"], ROSTER, lang="zh"),
          bots.summary_prompt("alpha", {}, "f", "m", lang="en"), bots.summary_prompt("alpha", {}, "f", "m", lang="zh"),
          mine, zh, "[[ShellBot: this waited 40 min in the queue before it went in]]")))
task = bots._BRIEF["en"]["head"].format(label="alpha") + "\nRules:\n1. x\n5. y" + bm._SEP + "write the quarterly note"
check("a brief that came with a task (delegate, A2A to a sleeping bot): the task is conversation", bm.real_text(task) == "write the quarterly note")
check("a person's words, another agent's message, a compaction and Codex's empty turn-start record",
      bm.classify("tidy the list")[0] == "said" and bm.classify("[[A2A: from research]]\nfind the file")[0] == "said"
      and bm.classify("This session is being continued from a previous conversation. Summary: …")[0] == "none"
      and bm.classify("")[0] == "none")
S = LONG_AGO
check("a message, a tool call and an answer are conversation", bm.substantive(WORK, S))
check("a wake-up and its 'ready' are not", not bm.substantive([u(brief, S + 1), a(S + 2, "alpha is ready")], S))
check("a 'hi' and its answer are not (two turns)", not bm.substantive([u("hi", S + 1), a(S + 2, "hello")], S))
check("…three turns are", bm.substantive([u("hi", S + 1), a(S + 2, "hello"), u("list my tasks", S + 3)], S))
check("an answer with nobody asking is not", not bm.substantive([a(S + 1), tool(S + 2), a(S + 3)], S))
check("only what came after the last save counts", not bm.substantive(WORK, S + 25) and bm.substantive(WORK, S - 5))
check("only what came after the last request ShellBot made counts",
      not bm.substantive(WORK + [u(mine, S + 40), a(S + 50, bots.SAVED_MARK)], S)
      and bm.substantive(WORK + [u(mine, S + 40), a(S + 50)] + [u("one more thing", S + 60), tool(S + 61), a(S + 62)], S))
check("turns with no time are placed by the last request", bm.substantive(
    [{"kind": "user_msg", "text": "x"}, {"kind": "assistant_text", "text": "y"}, {"kind": "tool_call"}], 0)
    and not bm.substantive([{"kind": "user_msg", "text": "x"}, {"kind": "tool_call"}, {"kind": "user_msg", "text": mine}], 0))
check("Codex's empty turn-start records neither reset nor count", bm.substantive(
    [u("", S + 1), u("do the thing", S + 2), u("", S + 3), tool(S + 4), a(S + 5)], S))
check("the turn that ended after a request is recognised (so nobody is pushed 'done')",
      bm.ended_by_request([u("real work", 1), a(2), u(mine, 3), a(4, bots.SAVED_MARK)])
      and not bm.ended_by_request([u(mine, 3), a(4), u("and then this", 5), a(6)]) and not bm.ended_by_request([]))

print("4. who is looked at")
api = reset()
api.sessions["s4"] = Tab("s4", "outside", "zsh")
check("a memory bot's tab is asked", why(api) == "", why(api))
api = reset()
api.sessions["s9"] = Tab("s9", "codex", "codex")
write_config({"bot_memory_autosave": False})
check("setting off: nobody is looked at", bm.tick(api, cfg(), now=NOW) == [] and not api.sent)
write_config({"experimental_bots": False})
check("bots off: nobody is looked at", bm.tick(api, cfg(), now=NOW) == [] and not api.sent)
api = reset()
res = bm.tick(api, cfg(), now=NOW)
check("only a memory bot's tab is a candidate: not memory off (s2), not a temporary bot or a plain role's tab (s3)",
      [r[0] for r in res] == ["s1"] and res[0][2] == "asked", res)
api = reset()
api.sessions["s1"].cmd = "zsh"
check("a tab that is no AI CLI is never typed into", why(api) == "provider")
api.sessions["s1"].cmd = "agy"
check("…nor one whose conversation ShellBot cannot read", why(api) == "provider")
for cmd in ("claude", "codex", "opencode", "grok", "cursor-agent", "pi"):
    api.sessions["s1"].cmd = cmd
    if bm.agent_status.worker_kind(cmd) not in bm.PROVIDERS:
        check(f"{cmd} is not a provider", False)
check("claude, codex, opencode, grok, cursor and pi are the CLIs it works on", all(
    bm.agent_status.worker_kind(c) in bm.PROVIDERS for c in ("claude", "codex", "opencode", "grok", "cursor-agent", "pi")))

print("5. not now")
api = reset()
check("idle for less than the setting: not yet; at the setting, yes",
      why(api, now=api.activity["s1"] + 44 * 60 + 29) == "active" and why(api, now=api.activity["s1"] + 45 * 60) == "")
api.sessions["s1"]._last_user_activity_time = NOW - 20 * 60
check("a person typed 20 minutes ago: not yet", why(api) == "active")
check("…with the idle time set to 10 minutes, now", why(api, mins=10) == "")
api.sessions["s1"]._last_user_activity_time = LONG_AGO
api.activity["s1"] = NOW - 5 * 60
check("the agent did something 5 minutes ago (its transcript): not yet", why(api) == "active")
api.activity["s1"] = LONG_AGO + 30

for st, word in (("working", "working"), ("decision", "decision")):
    api.state["s1"] = st
    check(f"a tab that is {st} is not asked", why(api) == word)
api.state["s1"] = ""
api.telegram_busy["s1"] = True
check("a Telegram turn in flight: not asked", why(api) == "telegram")
api.telegram_busy["s1"] = False
api.screen["s1"] = box("\x1b[39m❯\xa0half a message the person is typing")
check("the person's draft in the input box: not asked", why(api) == "draft")
check("…the draft is read the way tab_delivery reads it", td.draft_of(api, "s1").startswith("half a message"))
api.screen["s1"] = box("\x1b[39m❯\xa0\x1b[2mTry something\x1b[0m")
check("a dim suggestion is not the person's draft", why(api) == "")
api.screen["s1"] = ""
td._queues["s1"] = __import__("collections").deque([{"sid": "s1", "text": "x", "t0": time.time()}])
check("texts queued for the tab: not asked (it would jump the line)", why(api) == "queued")
td._queues.clear()
bridge_home._red["s1"] = {"reason": "Which one?", "options": [(1, "A"), (2, "B")], "at": LONG_AGO + 40}
check("the bot's own [[SF:RED]] question waits for the person: not asked", why(api) == "question")
api.turns["s1"] = WORK + [u("2", LONG_AGO + 50), a(LONG_AGO + 60), tool(LONG_AGO + 61)]
check("…once the person has answered it (a turn after it), it is", why(api) == "")
api.turns["s1"] = list(WORK)
api.prompt_at["s1"] = LONG_AGO + 45
check("…also by the hook's time of the person's last prompt", why(api) == "")
api.prompt_at.clear()
bridge_home._red.clear()
api._bots_closing = lambda sid: sid == "s1"
check("the tab is closing (writing its memory to close): not asked", why(api) == "closing")
api._bots_closing = lambda sid: False
bm._flight["s1"] = NOW
check("the last request is still being written: not asked", why(api) == "writing")
bm._flight.clear()
api.turns["s1"] = [u(brief, LONG_AGO + 1), a(LONG_AGO + 2, "alpha is ready")]
check("just woken and idle since: nothing to save", why(api) == "nothing_new")
api.turns["s1"] = list(WORK)
os.makedirs(bots.sessions_dir("alpha"), exist_ok=True)
bots.memory_file("alpha").write_text("- already saved\n", encoding="utf-8")
os.utime(bots.memory_file("alpha"), (LONG_AGO + 100, LONG_AGO + 100))
check("saved (MEMORY.md written) since it last did anything: nothing to save", why(api) == "saved")
os.utime(bots.memory_file("alpha"), (LONG_AGO - 600, LONG_AGO - 600))
check("…saved before its last work: the work is still unsaved", why(api) == "")
bots.memory_file("alpha").unlink()

print("6. asking")
api = reset()
before_user, before_act = api.sessions["s1"]._last_user_activity_time, api.sessions["s1"]._last_activity_time
res = bm.tick(api, cfg(), now=NOW)
check("the request went in, to the bot's tab, once", res == [("s1", "alpha", "asked")] and len(api.sent) == 1 and api.sent[0][0] == "s1", (res, api.sent))
text = api.sent[0][1]
sess_file = Path(__import__("re").search(r"to (\S+\.md) \(", text).group(1))
check("the summary goes to the bot's own sessions folder, named by time and tab",
      sess_file.parent == bots.sessions_dir("alpha") and sess_file.name.endswith("-s1.md"), sess_file)
check("what is sent is the request, MEMORY.md is the bot's", text.startswith(bm.MARK) and str(bots.memory_file("alpha")) in text)
check("ShellBot typing it is not the person at the keyboard: the idle reaper's clock is as it was",
      api.sessions["s1"]._last_user_activity_time == before_user and api.sessions["s1"]._last_activity_time == before_act)
check("while it is written it is not asked again", why(api) == "writing" or why(api) in ("working", "writing"))
check("…the request ends when the bot's turn does", wait_for(lambda: "s1" not in bm._flight))
check("the bot wrote its summary and MEMORY.md", sess_file.read_text(encoding="utf-8").startswith("# summary")
      and "short notes" in bots.memory_file("alpha").read_text(encoding="utf-8"))
log = LOG.read_text(encoding="utf-8") if LOG.exists() else ""
line = next((ln for ln in log.splitlines() if "autosave sid=s1" in ln and "written=" in ln), "")
check("the log says what was written", "written=" in line and sess_file.name in line and "MEMORY.md" in line, log[-400:])
print("     log:", line.split("] ", 1)[-1][:200])
api.state["s1"] = ""
api.activity["s1"] = time.time() - 3600                    # the save's own turn is in the transcript now
api.turns["s1"] = WORK + [u(text, time.time() - 120), a(time.time() - 110, bots.SAVED_MARK)]
check("afterwards nothing new: not asked again", bm.tick(api, cfg(), now=time.time() + 3 * 3600)[0][2] in ("nothing_new", "saved"))
later = time.time() + 2 * 3600
api.turns["s1"] += [u("now fix the other list", later - 7000), tool(later - 6990), a(later - 6980)]
api.activity["s1"] = later - 6980
api.sent.clear()
res = bm.tick(api, cfg(), now=later)
check("new work and another quiet spell: asked again", res == [("s1", "alpha", "asked")] and len(api.sent) == 1, res)
text2 = api.sent[0][1]
check("…into the same summary file (one per conversation, not one a day)",
      __import__("re").search(r"to (\S+\.md) \(", text2).group(1) == str(sess_file))
wait_for(lambda: "s1" not in bm._flight)

print("7. never over the person, never in a queue")
api = reset()
api.screen["s1"] = box("\x1b[39m❯\xa0unsent words")
res = bm.tick(api, cfg(), now=NOW)
check("a draft: nothing typed, nothing queued, and it waits for the next look", res == [("s1", "alpha", "draft")] and not api.sent and not td.pending("s1"))
api.screen["s1"] = ""
res = bm.tick(api, cfg(), now=NOW)
check("…which asks when the draft is gone", res == [("s1", "alpha", "asked")] and len(api.sent) == 1)
wait_for(lambda: "s1" not in bm._flight)
api = reset()
api.state["s1"] = "working"
check("a running turn: refused by tab_delivery without queueing", bm.start(api, "s1", "alpha", ROSTER["alpha"])["success"] is False
      and not td.pending("s1") and not api.sent and "s1" not in bm._flight)
api.state["s1"] = ""

print("8. a request nobody answers")
api = reset()
api.bot_writes = False
api.sessions["s1"]._last_user_activity_time = LONG_AGO
bm.tick(api, cfg(), now=NOW)
check("the bot does not write: the request still ends, and it is logged as nothing written",
      wait_for(lambda: "s1" not in bm._flight) and "written=nothing" in LOG.read_text(encoding="utf-8").splitlines()[-1], LOG.read_text(encoding="utf-8")[-300:])
check("…and the tab is not asked again until there is new work", why(api, now=time.time() + 3 * 3600) in ("saved", "nothing_new"))

print("9. the thread")
api = reset()
calls = []
bm.tick = lambda api_, cfg_, now=None: calls.append(1) or []
bm.TICK_SEC = 0.05
api._bot_memory_start()
api._bot_memory_start()
check("it looks again and again, one thread", wait_for(lambda: len(calls) >= 3) and sum(
    1 for t in threading.enumerate() if t.name == "bot-memory") == 1)
bm.tick = lambda api_, cfg_, now=None: []      # the thread goes on looking, at nothing: no real tick is left running against a fake tab
bm.TICK_SEC = 3600

print("10. what the chat view and the push read")
js = (HERE / "web" / "chat.js").read_text(encoding="utf-8")
check("web/chat.js folds on the same first line as the request", f"'{bm.MARK}'" in js)
push = (HERE / "api_push.py").read_text(encoding="utf-8")
check("a finished save is not pushed to the phone as 'done'", "bot_memory.ended_by_request(evs)" in push)

print(f"\n{passed} passed, {failed} failed")
import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
if failed:
    sys.exit(1)
print("ALL PASS")
