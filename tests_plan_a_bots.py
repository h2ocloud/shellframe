#!/usr/bin/env python3
"""Plan A on the phone's bot view and for keeping a bot (docs/mobile-link.md, the plan A
endpoint spec): every tab says what it is (a bot, a temporary bot, a terminal), its name,
CLI, model, Telegram /N and place; every role is listed with whether it remembers; the CLI
picker's choices; the fields a v1 phone reads are all still there. Keeping a temporary bot:
it becomes a bot with the face it had, its tab bound and named; each refusal has its code.

Demo roster and tabs. Run: .venv/bin/python tests_plan_a_bots.py
"""
import json
import os
import sys
import tempfile
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-plan-a-bots-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import api_bots  # noqa: E402
import bots  # noqa: E402
import main  # noqa: E402
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


def fresh(bots_on=True):
    main.CONFIG_FILE.write_text(json.dumps({
        "settings": {"experimental_bots": bots_on},
        "agent_roster": {
            "Coding": {"label": "Coding", "cmd": "codex", "bot": {"memory": True}},
            "時程信件": {"label": "時程信件-CLD", "cmd": "claude"},
            "總管": {"label": "總管", "cmd": "zsh", "coordinator": True,
                    "bot": {"external": {"kind": "webhook", "url": "https://example.com/hook"}}},
        },
        "bots_sessions": {"s1": {"bot": "Coding"}, "s3": {"avatar": {"shape": "egg", "color": "teal"}},
                          "s5": {"plain": True}},
    }, ensure_ascii=False), encoding="utf-8")


class Tab:
    def __init__(self, sid, label, cmd):
        self.sid, self._custom_label, self.cmd, self.alive = sid, label, cmd, True
        self._tmux_name, self._recent, self.lock = None, bytearray(), threading.Lock()
        self._last_output_activity_time = 0.0


STATUS = {"s1": {"state": "working", "action": "Running tests", "model": {"name": "GPT-5.5", "effort": "high"}},
          "s3": {"state": "done", "task": "查好了", "model": {"name": "Sonnet 5.5", "effort": "xhigh"}},
          "s4": {"state": "idle"}}
api = Api.__new__(Api)
api.sessions = {sid: Tab(sid, label, cmd) for sid, label, cmd in (
    ("s1", "Coding", "codex"), ("s3", "查資料", "claude --model sonnet"), ("s4", "zsh", "zsh"),
    ("s5", "草稿", "claude"), ("s6", "時程信件-CLD", "claude"))}
api.line_bridge = None
api.bridge = types.SimpleNamespace(_slot_order=["s3", "s1", "s5"])           # s4 and s6 are not on Telegram
api._window = None
api._agent_status_snapshot = lambda sid: (dict(STATUS.get(sid, {}), prompt_at=0.0), 0)
api._agent_state_for_list = lambda sid: STATUS.get(sid, {}).get("state", "")
api._agent_blocked_for_list = lambda sid: ""
api._last_activity_at = lambda sid, s, out_ts=0.0: 0.0
api._ordered_sids = lambda cfg=None, preferred_order=None: ["s3", "s1", "s4", "s5", "s6"]
api._bots_notify_ui = lambda: None
renamed = []
api.rename_session = lambda sid, name, manual=False: (renamed.append((sid, name, manual)),
                                                      setattr(api.sessions[sid], "_custom_label", name), '{"success": true}')[2]
api_bots._CLIS.update(at=0.0, list=[])
fresh()

print("1. GET /link/bots: version 2, what each tab is")
res = api._execute_sfctl("bots_link", {})
tabs = res["tabs"]
check("v is 2", res.get("v") == 2, res.get("v"))
check("a bound tab is a bot, named by the bot", tabs["s1"]["kind"] == "bot" and tabs["s1"]["name"] == "Coding", tabs["s1"])
check("an AI tab nobody kept is a temporary bot, named by its title", tabs["s3"]["kind"] == "temp"
      and tabs["s3"]["name"] == "查資料", tabs["s3"])
check("a shell is a terminal", tabs["s4"]["kind"] == "terminal" and tabs["s4"]["cli"] == "", tabs["s4"])
check("the CLI and the model's name", tabs["s1"]["cli"] == "codex" and tabs["s1"]["model"] == "GPT-5.5"
      and tabs["s3"]["cli"] == "claude" and tabs["s3"]["model"] == "Sonnet 5.5", (tabs["s1"], tabs["s3"]))
check("Telegram's /N, 0 when the tab is not there", tabs["s3"]["tg"] == 1 and tabs["s1"]["tg"] == 2
      and tabs["s4"]["tg"] == 0, {s: t["tg"] for s, t in tabs.items()})
check("the place in the desktop's list", tabs["s3"]["position"] == 1 and tabs["s6"]["position"] == 5)
V1_TAB = {"bot", "avatar", "plain", "closing", "activity", "doing", "latest", "last_at", "blocked", "decision"}
check("every field a v1 phone reads is still there", all(V1_TAB <= set(t) for t in tabs.values()))

print("2. every role, and whether it remembers")
by = {b["role"]: b for b in res["bots"]}
check("a role without a bot block is listed, without memory, awake in the tab titled for it",
      by["時程信件"]["memory"] is False and by["時程信件"]["state"] == "awake" and by["時程信件"]["sid"] == "s6", by["時程信件"])
check("a bot: memory, its CLI, its /N", by["Coding"]["memory"] is True and by["Coding"]["cli"] == "codex"
      and by["Coding"]["tg"] == 2, by["Coding"])
check("an external bot: a coordinator, no CLI of ours, no memory here",
      by["總管"]["coordinator"] is True and by["總管"]["cli"] == "" and by["總管"]["memory"] is False
      and by["總管"]["state"] == "external", by["總管"])
V1_BOT = {"role", "label", "responsibility", "model", "runs_on", "avatar", "primary", "external", "state", "sid",
          "position", "activity", "doing", "latest", "last_at", "blocked", "closing", "decision"}
check("every field a v1 phone reads is still there", all(V1_BOT <= set(b) for b in res["bots"]))
clis = {c["key"]: c for c in res["choices"]["clis"]}
check("the CLI picker: every CLI with its label, installed or not, and whether a model can be pinned",
      {"claude", "codex", "pi", "opencode", "agy", "grok", "cursor"} <= set(clis)
      and all(isinstance(c["installed"], bool) for c in clis.values())
      and clis["claude"]["models"] is True and clis["pi"]["models"] is False and clis["agy"]["models"] is False,
      clis)

print("3. keeping a temporary bot")
face = bots.resolved_avatar({"shape": "egg", "color": "teal"}, "s3")
r = api._execute_sfctl("bots_keep", {"sid": "查資料"})                     # by its title
cfg = main.load_config()
check("kept: a bot named by its title, its tab bound", r.get("success") and r.get("role") == "查資料"
      and (cfg["bots_sessions"].get("s3") or {}).get("bot") == "查資料", r)
entry = cfg["agent_roster"].get("查資料") or {}
check("…with the face it had (not one made from its name), its command, memory on",
      bots.resolved_avatar((entry.get("bot") or {}).get("avatar"), "查資料") == face
      and entry.get("cmd") == "claude --model sonnet" and bots.is_memory_bot(entry), entry)
check("…and a memory folder", (TMP / "bots").exists() and any((TMP / "bots").iterdir()))
check("its tab keeps the name, for good", ("s3", "查資料", True) in renamed or api.sessions["s3"]._custom_label == "查資料")
check("now the view says bot", api._execute_sfctl("bots_link", {})["tabs"]["s3"]["kind"] == "bot")


def code(sid, name=""):
    return (api._bots_keep(sid, name) or {}).get("code")


check("again: already a bot", code("s3") == "already_bot")
check("a terminal: not an AI", code("s4") == "not_ai")
check("a tab marked not a bot", code("s5") == "plain")
check("no such tab", code("s99") == "no_tab")
check("a name with a slash", code("s6", "a/b") == "bad_name")
api.sessions["s7"] = Tab("s7", "coding", "claude")
r = api._bots_keep("s7")
check("a name another bot has (any case): refused, with a free one to offer",
      r.get("code") == "name_taken" and r.get("suggestion") == "coding 2", r)
fresh(bots_on=False)
check("bots off", code("s7") == "bots_off")
fresh()
src = (HERE / "frame_link.py").read_text(encoding="utf-8")
check("a phone reaches it: POST /link/bots/keep is routed", '"/link/bots/keep"' in src)

print("4. a temporary bot is named from its first message")
import time  # noqa: E402
main._haiku_slug = lambda text: "weekly-report" if text else ""
tmux_calls = []
real_run = main.subprocess.run
main.subprocess.run = lambda args, **kw: (tmux_calls.append(list(args)), types.SimpleNamespace(
    returncode=1 if "has-session" in args else 0, stdout="", stderr=""))[1]      # no such session: the name is free
api._persist_session_manifest = lambda: None
renamed.clear()


def wait_renamed(sid):
    for _ in range(40):
        if any(r[0] == sid for r in renamed):
            return True
        time.sleep(0.05)
    return False


t8 = api.sessions["s8"] = Tab("s8", "claude", "claude")
t8._slug_pending = True
api._auto_name("s8", t8, "幫我整理週報")
check("no tmux (Windows): the tab is renamed, not left 'claude'", wait_renamed("s8")
      and ("s8", "weekly report", False) in renamed and not tmux_calls, (renamed, tmux_calls))
renamed.clear()
api._auto_name("s8", t8, "再一句")
time.sleep(0.2)
check("only once", not renamed)
t9 = api.sessions["s9"] = Tab("s9", "claude", "claude")
t9._slug_pending, t9._tmux_name = True, main.TMUX_PREFIX + "s9"
api._auto_name("s9", t9, "查一下")
check("with tmux: its session renamed too", wait_renamed("s9") and any("rename-session" in c for c in tmux_calls)
      and t9._tmux_name == main.TMUX_PREFIX + "weekly-report", (tmux_calls[:3], t9._tmux_name))
main.subprocess.run = real_run
t10 = api.sessions["s10"] = Tab("s10", "claude", "claude")
t10._slug_pending = True
real_exec = api._execute_sfctl
api._execute_sfctl = lambda cmd, args=None: {"success": False, "message": "busy"}
api.chat_send("s10", "第一句")
time.sleep(0.2)
check("the chat view: a send that failed names nothing", t10._slug_pending is True)
api._execute_sfctl = lambda cmd, args=None: {"success": True, "details": {}}
renamed.clear()
api.chat_send("s10", "第一句")
check("…one that went in names the tab, as Enter in the terminal does", wait_renamed("s10") and t10._slug_pending is False)
api._execute_sfctl = real_exec

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
