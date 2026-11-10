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
import agent_status  # noqa: E402
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
agent_status_kind = agent_status.worker_kind


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
check("v stays 1 (an app turns its bot view off for v > 1); plan A is told by kind on a tab",
      res.get("v") == 1 and all("kind" in t for t in res["tabs"].values()), res.get("v"))
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
UUID = "854ad0c0-2940-4d8c-a732-bc35ba7936b5"
api.sessions["s11"] = Tab("s11", "pinned", f"claude --model haiku --session-id {UUID}")
r = api._bots_keep("s11")
check("a kept bot wakes in a new conversation: the session id ShellBot gave its tab is not kept",
      r.get("success") and (main.load_config().get("agent_roster") or {}).get(r.get("role"), {}).get("cmd") == "claude --model haiku", r)
import launch_cmd  # noqa: E402
check("…nor --resume <uuid> or --continue; a value that is not a uuid, and a shell's -c, stay",
      launch_cmd.without_session(f"claude --resume {UUID} --continue --model opus") == "claude --model opus"
      and launch_cmd.without_session("claude -r notauuid") == "claude -r notauuid"
      and launch_cmd.without_session("bash -c 'cd ~/x && claude'") == "bash -c 'cd ~/x && claude'")
fresh(bots_on=False)
check("bots off", code("s7") == "bots_off")
fresh()
src = (HERE / "frame_link.py").read_text(encoding="utf-8")
check("a phone reaches it: POST /link/bots/keep is routed", '"/link/bots/keep"' in src)

print("4. a temporary bot is named from its first message")
import time  # noqa: E402
real_haiku_slug = main._haiku_slug          # section 8 tests the real one
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

print("5. Telegram /list: what each tab is, the bots asleep, the temporary ones")
import bridge_home  # noqa: E402
fresh()
cfg = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
cfg["agent_roster"]["研究"] = {"label": "研究", "cmd": "claude", "bot": {"memory": True}}
main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
for sid in ("s8", "s9", "s10"):
    api.sessions.pop(sid, None)
api.sessions["s3"]._custom_label = "查資料"
bridge = types.SimpleNamespace(home_api=api, config=types.SimpleNamespace(bot_token="x"))
sent = []
bridge_home.SEND = lambda method, payload: sent.append((method, payload)) or {"ok": True}
check("a terminal and a temporary bot are marked on /list; a bot is not",
      bridge_home.kind_note(bridge, "s4") == "（終端機）" and bridge_home.kind_note(bridge, "s7") == "（臨時）"
      and bridge_home.kind_note(bridge, "s1") == "", [bridge_home.kind_note(bridge, x) for x in ("s4", "s7", "s1")])
sent.clear()
check("after /list, one more message", bridge_home.after_list(bridge, 42) and len(sent) == 1)
msg = sent[0][1] if sent else {}
buttons = [b for row in (msg.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]
check("the bots asleep, each with Wake", "休息中：研究" in msg.get("text", "")
      and {"text": "叫醒 研究", "callback_data": "bl:0"} in buttons, msg)
check("the external ones, and how to reach them", "在 ShellBot 外面：總管" in msg.get("text", ""))
check("the temporary bots, each with Keep (by name and /N, not a tab id)",
      "臨時 bot" in msg.get("text", "") and any(b["text"].startswith("記住 ") and "coding" in b["text"] for b in buttons), buttons)
check("every button's data is short (Telegram: 64 bytes)", all(len(b["callback_data"].encode()) <= 64 for b in buttons))
woke = []
real_wake = bridge_home._wake
bridge_home._wake = lambda br, role, label, chat_id: woke.append(role)
sent.clear()
check("Wake: tapped, answered, and the bot is woken", bridge_home.on_list_button(bridge, "bl:0", 42, "cq1")
      and sent[0][0] == "answerCallbackQuery" and "正在叫醒 研究" in sent[-1][1]["text"])
time.sleep(0.2)
check("…on a thread of its own", woke == ["研究"], woke)
bridge_home._wake = real_wake
bridge_home._waking.clear()
keep_i = next(int(b["callback_data"][3:]) for b in buttons if b["text"].startswith("記住 ") and "coding" in b["text"])
sent.clear()
bridge_home.on_list_button(bridge, f"bl:{keep_i}", 42, "cq2")
check("Keep: refused the way the desktop refuses it (another bot has the name), said in the chat",
      "已經有別的 bot 在用" in sent[-1][1]["text"], sent[-1:])
sent.clear()
check("a button from an older list: said, nothing done", bridge_home.on_list_button(bridge, "bl:99", 42, "cq3")
      and "舊了" in sent[0][1].get("text", ""), sent)
check("not ours: left to the bridge", bridge_home.on_list_button(bridge, "choice:s1:1", 42, "cq4") is False)
fresh(bots_on=False)
sent.clear()
check("bots off: /list as it was", not bridge_home.after_list(bridge, 42) and bridge_home.kind_note(bridge, "s4") == "" and not sent)
fresh()
bridge_home.SEND = None

print("6. another CLI or model for a bot")
fresh()
cfg = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
cfg["agent_roster"]["研究"] = {"label": "研究", "cmd": "claude", "model": "opus", "bot": {"memory": True}}
main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
api_bots._CLIS.update(at=time.time() + 3600, list=[
    {"key": "claude", "label": "Claude Code", "installed": True, "models": True},
    {"key": "codex", "label": "Codex", "installed": True, "models": True},
    {"key": "pi", "label": "Pi", "installed": True, "models": False},
    {"key": "agy", "label": "Antigravity", "installed": False, "models": False}])
api.preset_groups = lambda: json.dumps([{"provider": "claude", "items": [{"cmd": "claude --permission-mode default"}]}])
opened, closed = [], []
api.bots_open = lambda role: (opened.append(role), json.dumps({"success": True, "details": {"sid": "s77"}}))[1]


def close_now(sid, mode="save"):
    closed.append((sid, mode))
    api.sessions.pop(sid, None)
    return json.dumps({"success": True})


api.bots_close = close_now
roster = lambda: json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))["agent_roster"]
r = api._bots_switch("研究", "codex")
check("next wake: the command for codex, and the model back to the CLI's own (opus would not fit codex)",
      r.get("success") and r.get("applies") == "next_wake" and agent_status_kind(roster()["研究"]["cmd"]) == "codex"
      and roster()["研究"]["model"] == "" and not opened, (r, roster()["研究"]))
r = api._bots_switch("研究", "claude", "sonnet")
check("back to claude: this computer's own claude preset (its flags), with the model given",
      roster()["研究"]["cmd"] == "claude --permission-mode default" and roster()["研究"]["model"] == "sonnet", roster()["研究"])
check("its face, job and memory stay (the bot block untouched)", roster()["研究"]["bot"] == {"memory": True})
check("a CLI that picks its model inside: a model is refused", api._bots_switch("研究", "pi", "x").get("code") == "model_fixed")
check("a CLI not installed here", api._bots_switch("研究", "agy").get("code") == "bad_cli")
check("an external bot", api._bots_switch("總管", "claude").get("code") == "external")
check("no such bot", api._bots_switch("沒有", "claude").get("code") == "no_bot")
api._bots_switch("時程信件", "codex")
check("a role without memory stays without: only its command changes",
      "bot" not in roster()["時程信件"] and agent_status_kind(roster()["時程信件"]["cmd"]) == "codex", roster()["時程信件"])
r = api._bots_switch("研究", "", None, "now")
check("now, asleep: woken at once on its command", r.get("success") and r.get("sid") == "s77" and opened == ["研究"], r)
opened.clear()
r = api._execute_sfctl("bots_switch", {"role": "Coding", "cli": "claude", "now": True})
check("now, awake (sfctl bots-set --now): pending, while it writes its memory", r.get("success") and r.get("pending"), r)
for _ in range(60):
    if opened:
        break
    time.sleep(0.1)
check("…its tab closed the bot's way (memory first), then woken on the new CLI",
      closed == [("s1", "save")] and opened == ["Coding"] and agent_status_kind(roster()["Coding"]["cmd"]) == "claude",
      (closed, opened))
r = api._bots_link_settings({"role": "研究", "cli": "codex", "apply": "next_wake"})
check("the phone's /link/bots/settings with cli: the same switch", r.get("success") and agent_status_kind(roster()["研究"]["cmd"]) == "codex", r)

print("7. the one-time tidy (sfctl bots-migrate)")
fresh()
cfg = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
cfg["agent_roster"]["研究"] = {"label": "研究-CLD", "cmd": "claude", "bot": {"memory": True}}
cfg["session_order"] = ["s1", "s3", "s4"]
main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
api.sessions = {sid: Tab(sid, label, cmd) for sid, label, cmd in (
    ("s1", "Coding", "codex"), ("s21", "研究-CLD", "claude"), ("s5", "研究-CLD", "claude"), ("s4", "zsh", "zsh"))}
api.bridge = None
main.load_config()                    # a first read fills in what an old config lacks (ui_style): not the tidy's doing
before = main.CONFIG_FILE.read_text(encoding="utf-8")
r = api._execute_sfctl("bots_migrate", {})
check("a dry run lists the binding a title implies, and writes nothing",
      r.get("bindings") == [{"sid": "s21", "title": "研究-CLD", "bot": "研究"}] and not r.get("apply")
      and main.CONFIG_FILE.read_text(encoding="utf-8") == before, r)
r = api._execute_sfctl("bots_migrate", {"apply": True})
after = json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))
check("applied: the binding written, config.json copied first",
      (after["bots_sessions"].get("s21") or {}).get("bot") == "研究" and Path(r.get("backup", "")).exists()
      and Path(r["backup"]).read_text(encoding="utf-8") == before, r)
check("a tab marked not a bot is not touched", after["bots_sessions"].get("s5") == {"plain": True})
check("the roster's keys and the tabs' order (Telegram's /N) are as they were",
      sorted(after["agent_roster"]) == sorted(json.loads(before)["agent_roster"])
      and after.get("session_order") == ["s1", "s3", "s4"])
check("run again: nothing more", api._execute_sfctl("bots_migrate", {"apply": True}).get("bindings") == [])

print("8. the first message's name: `claude --print` gets the time it takes")
import subprocess  # noqa: E402
check("the name call waits long enough (12.8 s measured on a real Mac)", main.SLUG_TIMEOUT_S >= 20)
real_run, real_which = main.subprocess.run, main.shutil.which
main.shutil.which = lambda name: "/usr/local/bin/claude"


def slow(*a, **k):
    raise subprocess.TimeoutExpired(cmd="claude", timeout=k.get("timeout"))


main.subprocess.run = slow
check("a call that runs out of time: no name, and the debug log says so",
      real_haiku_slug("幫我記住一件事") == "" and "[slug] no name: TimeoutExpired" in Path(sf_log.DEBUG_LOG).read_text(encoding="utf-8"))
main.subprocess.run = lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="code-name-blue-whale\n")
check("an answer becomes the name", real_haiku_slug("幫我記住一件事") == "code-name-blue-whale")
main.subprocess.run, main.shutil.which = real_run, real_which
stub_slug = main._haiku_slug
t12 = api.sessions["s12"] = Tab("s12", "", "claude")
t12._slug_pending = True
main._haiku_slug = lambda text: (setattr(t12, "_custom_label", "手動取的名字"), "auto-made-name")[1]   # renamed while it ran
n0 = len(renamed)
api._auto_name("s12", t12, "第一句話")
time.sleep(0.3)
check("a name given by hand (or by keeping) while the name call ran stays",
      t12._custom_label == "手動取的名字" and not any(r[1] == "auto made name" for r in renamed[n0:]), renamed[n0:])
t13 = api.sessions["s13"] = Tab("s13", "", "claude")
t13._slug_pending = True
main._haiku_slug = lambda text: "auto-made-name"
api._auto_name("s13", t13, "第一句話")
time.sleep(0.3)
check("…and with no such rename, the tab takes the made name", t13._custom_label == "auto made name", t13._custom_label)
main._haiku_slug = stub_slug

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
