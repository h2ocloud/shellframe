#!/usr/bin/env python3
"""A bot renamed (plan A: one name per bot). From its tab (rename_session) and from its
editor (bots_rename) the same thing happens: its roster key and label, the tabs bound to
it, its groups, the idle reaper's keep list and its memory folder take the new name; the
old name joins its former names and still reaches it (A2A and [[SF:TO:…]] go through
agent_link.deliver, sfctl through the same resolver), and no other bot may take it.
Refused: a name another bot has or had, a bad name, a bot outside ShellBot, a bot writing
its memory, a folder already there. A temporary bot's or a terminal's rename changes only
the title; so does ShellBot naming a bot's tab after the bot.

Demo roster and tabs. Run: .venv/bin/python tests_bot_rename.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bot-rename-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import a2a_host  # noqa: E402
import agent_link  # noqa: E402
import api_bots  # noqa: E402
import bot_names  # noqa: E402
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
BOTS = TMP / "bots"
POSIX = os.name != "nt"


def demo_config(bots_on=True):
    return {
        "settings": {"experimental_bots": bots_on, "experimental_a2a": True},
        "agent_roster": {
            "研究": {"label": "研究-CLD", "cmd": "claude", "bot": {"memory": True}},
            "助理": {"label": "助理", "cmd": "claude", "bot": {"memory": True, "primary": True}},
            "Coding": {"label": "Coding", "cmd": "codex", "bot": {"memory": True}, "former_names": ["工程師"]},
            "時程信件": {"label": "時程信件-CLD", "cmd": "claude"},
            "外部": {"label": "外部", "cmd": "zsh",
                   "bot": {"external": {"kind": "webhook", "url": "https://example.com/hook"}}},
        },
        "bots_sessions": {"s1": {"bot": "助理"}, "s9": {"bot": "助理"}, "s2": {"bot": "Coding"}},
        "groups": {"調研組": {"roles": ["研究", "助理"], "created": 1}},
        "idle_reaper": {"keep_labels": ["main", "助理"]},
        "session_labels": {"s1": "助理", "s2": "Coding", "s3": "查資料", "s4": "zsh"},
    }


class Tab:
    def __init__(self, sid, label, cmd):
        self.sid, self._custom_label, self.cmd, self.alive = sid, label, cmd, True


api = Api.__new__(Api)
api.line_bridge = None
api._window = None
api._persist_session_manifest = lambda: None
SLOTS = {}
api.bridge = types.SimpleNamespace(slots=SLOTS, refresh_commands=lambda: None, _slot_order=["s1", "s2", "s3"])


def fresh(bots_on=True):
    """The demo config, its tabs (s9 is a closed tab still bound to 助理) and 助理's memory."""
    main.CONFIG_FILE.write_text(json.dumps(demo_config(bots_on), ensure_ascii=False), encoding="utf-8")
    api.sessions = {sid: Tab(sid, label, cmd) for sid, label, cmd in (
        ("s1", "助理", "claude --session-id 1"), ("s2", "Coding", "codex"),
        ("s3", "查資料", "claude"), ("s4", "zsh", "zsh"), ("s6", "研究-CLD", "claude"))}
    SLOTS.clear()
    SLOTS.update({sid: types.SimpleNamespace(label=api.sessions[sid]._custom_label) for sid in ("s1", "s2", "s3")})
    shutil.rmtree(BOTS, ignore_errors=True)
    d = bots.ensure_dirs("助理")
    (d / "MEMORY.md").write_text("記得：使用者最喜歡的鯨魚是藍鯨", encoding="utf-8")
    (d / "sessions" / "20261111-090000-s1.md").write_text("上次在整理發版清單", encoding="utf-8")


def cfg():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8"))


def names(c):
    """Every bot's names: its key, label and former names (saving the config fills other fields in)."""
    return {r: (e.get("label"), e.get("former_names")) for r, e in (c.get("agent_roster") or {}).items()}


def shape(c):
    """What a rename touches, to compare the two ways in."""
    return {k: c.get(k) for k in ("agent_roster", "bots_sessions", "groups", "idle_reaper", "session_labels")}


print("1. the config by itself (bots.rename_in_config)")
c = demo_config()
formers = bots.rename_in_config(c, "助理", "秘書")
check("the roster key moves and keeps its place in the order",
      list(c["agent_roster"]) == ["研究", "秘書", "Coding", "時程信件", "外部"], list(c["agent_roster"]))
e = c["agent_roster"]["秘書"]
check("its label is the new name, its old name a former name",
      e["label"] == "秘書" and e["former_names"] == ["助理"] == formers, e)
check("its face, job and the rest stay", e["bot"] == {"memory": True, "primary": True} and e["cmd"] == "claude", e)
check("every tab bound to it follows, an open one and a closed one",
      c["bots_sessions"]["s1"] == {"bot": "秘書"} and c["bots_sessions"]["s9"] == {"bot": "秘書"}
      and c["bots_sessions"]["s2"] == {"bot": "Coding"}, c["bots_sessions"])
check("its groups follow", c["groups"]["調研組"]["roles"] == ["研究", "秘書"], c["groups"])
check("the idle reaper's keep list follows", c["idle_reaper"]["keep_labels"] == ["main", "秘書"], c["idle_reaper"])
check("the other bots are untouched",
      c["agent_roster"]["研究"] == demo_config()["agent_roster"]["研究"]
      and c["agent_roster"]["Coding"] == demo_config()["agent_roster"]["Coding"])
bots.rename_in_config(c, "秘書", "助理")
check("back to a name it had: that one leaves its former names, the other joins",
      c["agent_roster"]["助理"]["former_names"] == ["秘書"], c["agent_roster"]["助理"])
c = demo_config()
check("an old label is kept as a former name too",
      bots.rename_in_config(c, "研究", "調查") == ["研究", "研究-CLD"], c["agent_roster"]["調查"])

print("2. the old name still finds it (bot_names)")
c = demo_config()
bots.rename_in_config(c, "Coding", "Builder")
roster = c["agent_roster"]
for name in ("Coding", "coding", "工程師", "Builder"):
    res = bot_names.resolve(name, roster, [], c["bots_sessions"])
    check(f"{name!r} finds Builder", res.get("ok") and res.get("role") == "Builder", res)
check("delegate and A2A's lookup too (Api._resolve_agent_role)", Api._resolve_agent_role("coding", roster)[0] == "Builder")
check("the start of an old name, when it is the only one", bot_names.resolve("Codi", roster, [], {}).get("role") == "Builder")

print("3. from its tab: rename_session on a bot's tab renames the bot")
fresh()
r = json.loads(api.rename_session("s1", "秘書", manual=True))
check("renamed", r.get("success") and r.get("role") == "秘書" and r.get("former") == "助理", r)
c = cfg()
check("roster, bound tabs, groups, keep list", "秘書" in c["agent_roster"] and "助理" not in c["agent_roster"]
      and c["bots_sessions"]["s1"]["bot"] == "秘書" and c["bots_sessions"]["s9"]["bot"] == "秘書"
      and c["groups"]["調研組"]["roles"] == ["研究", "秘書"] and c["idle_reaper"]["keep_labels"] == ["main", "秘書"], shape(c))
check("the tab, Telegram's list and the saved label carry it",
      api.sessions["s1"]._custom_label == "秘書" and SLOTS["s1"].label == "秘書" and c["session_labels"]["s1"] == "秘書")
check("the memory folder moved with it", (BOTS / "秘書" / "MEMORY.md").read_text(encoding="utf-8").endswith("藍鯨"))
if POSIX:
    check("a link is left at the old folder's path", (BOTS / "助理").is_symlink()
          and (BOTS / "助理").resolve() == (BOTS / "秘書").resolve())
by_tab = shape(c)

print("4. from its editor: bots_rename does exactly the same")
fresh()
r = json.loads(api.bots_rename("助理", "秘書"))
check("renamed", r.get("success") and r.get("role") == "秘書", r)
check("the config is the same as renaming the tab", shape(cfg()) == by_tab, shape(cfg()))
check("and the tab is named after it", api.sessions["s1"]._custom_label == "秘書" and SLOTS["s1"].label == "秘書")
check("by sfctl too (bots-rename takes an old name)",
      api._execute_sfctl("bots_rename", {"role": "秘書", "name": "秘書長"}).get("role") == "秘書長"
      and "秘書長" in cfg()["agent_roster"])
fresh()
out = api._execute_sfctl("rename", {"sid": "助理", "name": "秘書"})
check("sfctl rename on the bot's tab (by its name) renames the bot and says so",
      out.get("success") and out.get("former") == "助理" and "秘書" in cfg()["agent_roster"]
      and "舊名字" in out.get("message", ""), out)

print("5. the old name keeps reaching it: A2A, [[SF:TO:…]], sfctl")
ctx = a2a_host._Ctx(api)
check("A2A to the old name finds its tab", ctx.resolve("助理", "") == ("s1", "秘書"), ctx.resolve("助理", ""))
check("…and its key is the new name", ctx.role_key("助理") == "秘書")
seen = {}


def fake_authorize(**kw):
    seen.update(kw)
    return False, "stopped by the test", 0


real_authorize = agent_link.authorize
agent_link.authorize = fake_authorize
try:
    agent_link.deliver(ctx, role="助理", sid="", text="hello", from_sid="s3", from_label="查資料")
finally:
    agent_link.authorize = real_authorize
check("deliver (what sfctl a2a-send and [[SF:TO:助理|…]] both call) authorizes the new name",
      seen.get("to_role") == "秘書", seen.get("to_role"))
check("sfctl's tab commands find its tab by the old name", api._sid_for("助理")[0] == "s1", api._sid_for("助理"))
api._agent_status_snapshot = lambda sid: ({"state": "idle", "prompt_at": 0.0}, 0)
api._agent_state_for_list = lambda sid: "idle"
row = next((r for r in api._execute_sfctl("bots_link", {}).get("bots", []) if r.get("role") == "秘書"), {})
check("a phone's bot view lists the old name (a message sent under it finds this bot's face)",
      row.get("former_names") == ["助理"], row)

print("6. names that are taken or not allowed")
for name, why in (("研究", "another bot's name"), ("研究-cld", "another bot's label, another case"),
                  ("工程師", "another bot's former name")):
    fresh()
    r = json.loads(api.rename_session("s1", name, manual=True))
    check(f"{name!r}: {why} is taken", r.get("code") == "name_taken" and r.get("suggestion"), r)
    check("…and nothing changed", "助理" in cfg()["agent_roster"] and api.sessions["s1"]._custom_label == "助理"
          and (BOTS / "助理").is_dir() and not (BOTS / "助理").is_symlink())
for name in ("a/b", "a:b", "ui"):
    fresh()
    check(f"{name!r} is refused", json.loads(api.bots_rename("助理", name)).get("code") == "bad_name")
fresh()
r = json.loads(api.bots_rename("助理", "助理"))
check("its own name: nothing to do", r.get("success") and cfg()["agent_roster"]["助理"].get("former_names") is None, r)
long = "很" * 45
r = json.loads(api.bots_rename("助理", long))
check("a name past 40 characters is cut, as Keep cuts it", r.get("role") == "很" * 40, r)
fresh()
api.bots_rename("助理", "秘書")
r = json.loads(api.bots_keep("s3", "助理"))
check("Keep cannot take a name a bot had", r.get("code") == "name_taken", r)
r = json.loads(api.bots_rename("秘書", "助理"))
check("the bot itself can go back to it", r.get("success") and "助理" in cfg()["agent_roster"], r)
if POSIX:
    check("…its folder too, and the link it left is gone", (BOTS / "助理").is_dir() and not (BOTS / "助理").is_symlink()
          and (BOTS / "秘書").is_symlink())

print("7. what is not renamed")
fresh()
check("a bot outside ShellBot", json.loads(api.bots_rename("外部", "X")).get("code") == "external")
api_bots._CLOSING["s1"] = {"role": "助理", "phase": "summary", "since": 0}
try:
    check("a bot writing its memory", json.loads(api.bots_rename("助理", "秘書")).get("code") == "busy")
finally:
    api_bots._CLOSING.pop("s1", None)
(BOTS / "秘書").mkdir()
(BOTS / "秘書" / "notes.md").write_text("someone else's", encoding="utf-8")
r = json.loads(api.bots_rename("助理", "秘書"))
check("a folder already there is not overwritten", r.get("code") == "folder_taken"
      and "助理" in cfg()["agent_roster"] and (BOTS / "秘書" / "notes.md").exists(), r)
fresh()
before = names(cfg())
api.rename_session("s3", "新的名字", manual=True)
check("a temporary bot's tab: only its title", names(cfg()) == before
      and api.sessions["s3"]._custom_label == "新的名字")
api.rename_session("s4", "shell", manual=True)
check("a terminal: only its title", names(cfg()) == before and api.sessions["s4"]._custom_label == "shell")
api.rename_session("s1", "助理", manual=True)
check("ShellBot naming a bot's tab after the bot changes no bot", names(cfg()) == before)
api.bots_save("助理", json.dumps({"responsibility": "排程"}), "s1")
check("nor does saving a bot bound to its tab", names(cfg()) == before
      and cfg()["agent_roster"]["助理"]["responsibility"] == "排程")
fresh(bots_on=False)
api.rename_session("s1", "秘書", manual=True)
check("bots off: a tab's rename is only a title", "助理" in cfg()["agent_roster"] and api.sessions["s1"]._custom_label == "秘書")

print("8. closed and woken again: its memory comes along")
fresh()
api.bots_rename("助理", "秘書")
mem, last, last_name = bots.read_memory("秘書")
check("its memory and last summary are read under the new name", "藍鯨" in mem and "發版清單" in last, (mem, last))
c = cfg()
brief = bots.compose_brief("秘書", c["agent_roster"]["秘書"], c["agent_roster"], memory=mem, last=last)
check("the brief it wakes with names it, its new folder and that memory",
      "秘書" in brief and str(BOTS / "秘書") in brief and "藍鯨" in brief)
check("the next summary goes to the new folder", bots.new_session_file("秘書", "s1").parent == BOTS / "秘書" / "sessions")
if POSIX:
    (BOTS / "助理" / "MEMORY.md").write_text("寫在舊的路徑", encoding="utf-8")
    check("a bot still awake that writes where its brief said lands in the new folder",
          (BOTS / "秘書" / "MEMORY.md").read_text(encoding="utf-8") == "寫在舊的路徑")

print("9. both ways in, in the page")
js = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
check("a tab's rename dialog hands a bot's tab to bots.js before its own rename",
      html.index("SFBots.renameBotTab(sid, val)") < html.index("s.label = val;", html.index("function renameSession")))
check("bots.js asks first, then renames through the tab (rename_session)",
      "renameBotTab" in js and "askRename(role, name)" in js[js.index("function renameBotTab"):]
      and "rename_session(sid, name, true)" in js)
ed = js[js.index("async function openEditor"):js.index("function interceptClose")]
check("the editor's name can be changed, except an outside bot's", "existing && isExternal(role) ? 'readonly'" in ed)
check("the editor asks the same question and renames with bots_rename before it saves",
      "askRename(role, name)" in ed and ed.index("bots_rename(role, name)") < ed.index("bots_save(saveAs"))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
