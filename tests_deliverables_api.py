#!/usr/bin/env python3
"""`sfctl deliver` and the end-of-turn auto-pickup (api_deliverables.py), with a stand-in app.

  - who registers is the calling tab: the bot it is, else the tab ("sid@opened": a later tab that gets the same id is another
    desk, and the first one's papers are in the closed-tabs group); a call with no tab is refused
  - add / list / show answer in one line each; a relative path was made absolute by sfctl's client; a file that does not exist,
    a task that does not exist, a bad state are refused in words
  - sfctl's parser has the verb and makes the path absolute on its side
  - auto-pickup: the status monitor's transition (a turn that really ran, then done) reads that turn's LAST assistant text once;
    the same reply is not read twice; a memory-save turn is not read; nothing is read while a turn is still working; the files it
    names become candidates (not on the desk, not counted)
  - the page's own buttons (`deliverables_act`) open, keep and take, and nothing but the person's buttons does (no sfctl verb)
  - `deliver` is reachable from the sfctl dispatcher only: no Frame Link route maps to it
Temp folders only. Demo data. Run: .venv/bin/python tests_deliverables_api.py
"""
import json
import os
import re
import sys
import tempfile
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

os.environ.setdefault("SHELLFRAME_TESTS", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import deliverables as dv  # noqa: E402
import state_store as ss  # noqa: E402
import api_deliverables  # noqa: E402
import api_office_objects  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


api_deliverables._ = lambda en, zh: zh
TMP = Path(tempfile.mkdtemp(prefix="sf-deliv-api-"))
os.environ["SHELLFRAME_STATE_DIR"] = str(TMP / "state")
ss.db_path = lambda: TMP / "state.db"
OPENED = []


class Main:
    IS_WIN = False
    _tmux_bin = staticmethod(lambda: None)
    load_config = staticmethod(lambda: {"agent_roster": {"示範助理": {"label": "示範助理", "bot": {"memory": True}}}})


api_deliverables.main = Main
import bot_memory  # noqa: E402
import agent_status  # noqa: E402


class App(api_deliverables.DeliverablesApiMixin, api_office_objects.OfficeObjectsApiMixin):
    def __init__(self):
        self.sessions = {"s1": types.SimpleNamespace(_custom_label="示範助理", _spawn_ts=1000.0, alive=True, _tmux_name=None),
                         "s9": types.SimpleNamespace(_custom_label="臨時分頁", _spawn_ts=2000.0, alive=True, _tmux_name=None)}
        self.events = []

    def _bots_role_of(self, sid):
        return "示範助理" if sid == "s1" else ""

    def _resolve_name(self, name, prefix=True):
        return {"ok": name in ("示範助理", "臨時分頁"), "role": "示範助理" if name == "示範助理" else "", "sid": "s9" if name == "臨時分頁" else ""}

    def _worker_ctx(self, sid, s):
        return {"sid": sid}

    def _bots_enabled(self):
        return True

    def open_url(self, url):
        OPENED.append(url)
        return json.dumps({"success": True})

    def open_local_file(self, path):
        OPENED.append(path)
        return json.dumps({"success": True})


app = App()
NOW = time.time()


def mk(rel, text="示範\n", age=30):
    p = TMP / "work" / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    os.utime(p, (NOW - age, NOW - age))
    return p


def deliver(sid, action, **kw):
    return app._deliv_sfctl({"action": action, "from_sid": sid, **kw})


print("who registers")
f1 = mk("週報.md")
r = deliver("s1", "add", ref=str(f1), title="示範週報", note="第 3 點是我猜的")
f1b, f1c, f1d = mk("b.md"), mk("c.md"), mk("d.md")
check("a bot's tab registers on the bot's desk", r["success"] and "示範助理" in r["message"] and r["details"]["state"] == "new", r)
row = dv.get(r["details"]["id"])
check("…the bot is the owner, by role", (row["owner_kind"], row["owner_key"]) == ("bot", "示範助理"))
r2 = deliver("s9", "add", ref=str(f1), title="另一份")
row2 = dv.get(r2["details"]["id"])
check("a plain tab registers on its own desk: sid + the time it was opened", row2["owner_kind"] == "tab" and row2["owner_key"] == "s9@2000", row2)
app.sessions["s9"] = types.SimpleNamespace(_custom_label="新的臨時分頁", _spawn_ts=5000.0, alive=True, _tmux_name=None)
r3 = deliver("s9", "add", ref=str(f1), title="新分頁交的")
check("a new tab that got the same id has another desk (it does not inherit the old tab's papers)",
      dv.get(r3["details"]["id"])["owner_key"] == "s9@5000" and r3["details"]["id"] != r2["details"]["id"])
ov = dv.overview(app._deliv_live_tabs())
check("…the old tab's paper is in the closed group, the new one on the open tab's desk", "x:s9@2000" in ov["by_owner"] and ov["by_owner"]["t:s9"]["count"] == 1, ov["by_owner"].keys())
check("a call from no tab is refused", not deliver("", "add", ref=str(f1))["success"] and not deliver("s77", "add", ref=str(f1))["success"])
check("a file that does not exist is refused in words", "找不到" in deliver("s1", "add", ref=str(TMP / "沒有.md"))["message"] or "not found" in deliver("s1", "add", ref=str(TMP / "沒有.md"))["message"])
check("a task that does not exist is refused", not deliver("s1", "add", ref=str(f1), task=99)["success"])
tk = ss.add("示範任務")
check("--task links it", dv.get(deliver("s1", "add", ref=str(f1b), title="連任務", task=tk["id"])["details"]["id"])["task_id"] == tk["id"])
check("a URL is registered as it is", deliver("s1", "add", ref="https://demo.example.test/preview", title="預覽站")["details"]["type"] == "url")
flagged = deliver("s1", "add", ref=str(mk("my-token.txt")), title="x")
check("a file that looks like a key is registered and the bot is told it is flagged", flagged["success"] and flagged["details"]["flag"] == "secret" and "金鑰" in flagged["message"])
check("a secret in --note never reaches the database", "abc123xyz" not in repr(dv.get(deliver("s1", "add", ref=str(f1c), title="n", note="API_TOKEN=abc123xyz")["details"]["id"])))

print("list and show")
ls = deliver("s1", "list")
check("list: this bot's undecided and kept ones, one line each", ls["success"] and len(ls["details"]["items"]) >= 4 and ls["message"].count("\n") >= 3 and "#" in ls["message"], ls["message"])
check("…never another owner's", all(dv.get(i["id"])["owner_key"] == "示範助理" for i in ls["details"]["items"]))
check("list --owner: a tab by its name", {dv.get(i["id"])["owner_key"] for i in deliver("s1", "list", owner="臨時分頁")["details"]["items"]} == {"s9@5000"})
check("list --state candidate is empty for now", deliver("s1", "list", state="candidate")["details"]["items"] == [])
check("a bad state is refused", not deliver("s1", "list", state="weird")["success"])
check("show N", deliver("s1", "show", id=str(row["id"]))["details"]["title"] == "示範週報" and "no such" not in deliver("s1", "show", id="#" + str(row["id"]))["message"])
check("show of nothing", not deliver("s1", "show", id="9999")["success"] and not deliver("s1", "show", id="x")["success"])
check("a list never carries a file's text", "示範\n" not in json.dumps(ls, ensure_ascii=False))

print("sfctl's side")
sf = (HERE / "sfctl.py").read_text(encoding="utf-8")
check("the parser has `deliver add|list|show` with --title --note --task --state --owner --json",
      all(x in sf for x in ('"deliver"', '"--title"', '"--note"', '"--task"', '"--state"', '"--owner"')) and "def _cmd_deliver" in sf)
check("a relative path is made absolute on sfctl's side", "os.path.abspath(os.path.expanduser(ref))" in sf)
remote = (HERE / "api_remote.py").read_text(encoding="utf-8")
check("the dispatcher has `deliver` and it calls _deliv_sfctl", 'elif cmd == "deliver":' in remote and "self._deliv_sfctl(args)" in remote)
link_srcs = "".join((HERE / n).read_text(encoding="utf-8") for n in ("frame_link.py", "api_link.py", "api_server.py", "link_bots.py"))
check("no Frame Link or HTTP route maps to deliver (nothing is sent to a paired computer)", not re.search(r'["\']deliver["\']', link_srcs) and "deliverables" not in link_srcs)
check("no sfctl verb moves a paper (keep / take / accept / dismiss are the page's buttons)",
      not re.search(r'add_parser\("(keep|take|accept|dismiss)', sf) and "dv.move" not in (HERE / "api_remote.py").read_text(encoding="utf-8"))

print("the page's buttons")
orow = deliver("s1", "add", ref=str(mk("給按鈕.pdf")), title="按鈕")["details"]["id"]
r = json.loads(app.deliverables_act(orow, "keep", "{}", "zh"))
check("keep", r["success"] and r["ok"] and dv.get(orow)["state"] == "kept")
r = json.loads(app.deliverables_act(orow, "open", "{}", "zh"))
check("open: the file is opened by the host, the page never gets a path", r["success"] and OPENED[-1].endswith("給按鈕.pdf") and "給按鈕" not in json.dumps(r, ensure_ascii=False))
r = json.loads(app.deliverables_act(orow, "take", "{}", "zh"))
check("take", r["success"] and dv.get(orow)["state"] == "taken")
check("an action the page may not call is refused (ask_change goes through the popup's chat send)", not json.loads(app.deliverables_act(orow, "ask_change", "{}", "zh"))["success"])
check("the overview carries the red number, per owner and the rail", app._deliv_overview()["new"] >= 1 and "rail" in app._deliv_overview())
tasks_src = (HERE / "api_tasks.py").read_text(encoding="utf-8")
check("tasks_overview carries it (one read for 待你決定 and the deliverables); the paired computers' read does not",
      '"deliverables"' in tasks_src and "_deliv_overview" in tasks_src and tasks_src.count("_deliv_overview") == 2)

print("auto-pickup at the end of a turn")
fresh = mk("交付/示範整理.xlsx", "x", age=60)
env_file = mk("交付/.env", "x", age=60)
REPLY = f"比較表整理好了，存在 {fresh}，A 方案便宜。設定檔 {env_file} 別碰。"
TURNS = {"events": [{"kind": "user_msg", "text": "請整理"}, {"kind": "assistant_text", "text": "先看看"}, {"kind": "assistant_text", "text": REPLY}]}
agent_status.resolve_transcript = lambda ctx: "/tmp/fake.jsonl"
agent_status._read_tail_events = lambda path, **kw: ("claude", TURNS["events"], None)
threads = []


class T:
    def __init__(self, target, args=(), daemon=None, name=None):
        self.t, self.a = target, args

    def start(self):
        self.t(*self.a)


api_deliverables.threading = types.SimpleNamespace(Thread=T)
WORKING = {"s1": {"state": "working"}}
DONE = {"s1": {"state": "done"}}
app._deliv_observe(DONE)                      # first sight of a tab: a baseline, no read
check("a tab that is already done when first seen is not read (a restart must not scan every tab)", not dv.listing(states=("candidate",)))
app._deliv_observe(WORKING)
check("while a turn is working nothing is read", not dv.listing(states=("candidate",)))
app._deliv_observe(DONE)
cands = dv.listing(states=("candidate",))
check("when the turn ends, the LAST assistant text is read: the file it names is a candidate for that bot, not on the desk",
      [Path(c["ref"]).name for c in cands] == ["示範整理.xlsx"] and cands[0]["owner_key"] == "示範助理" and cands[0]["origin"] == "auto"
      and "比較表整理好了" in cands[0]["source"], cands)
check("the key-looking file it also named is not listed; its name is kept for the 'skipped' line", dv.skipped_names(("bot", "示範助理")) == [".env"])
n = len(dv.listing())
app._deliv_observe(WORKING)
app._deliv_observe(DONE)
check("the same reply is not read twice", len(dv.listing()) == n)
TURNS["events"] = [{"kind": "user_msg", "text": bot_memory.MARK + " 請寫記憶"}, {"kind": "assistant_text", "text": f"已寫入 {mk('交付/記憶.md')}"}]
app._deliv_observe(WORKING)
app._deliv_observe(DONE)
check("a turn that answered the memory-save request is not read", not any(Path(c["ref"]).name == "記憶.md" for c in dv.listing(states=("candidate",))))
check("candidates are not counted", dv.overview(app._deliv_live_tabs())["new"] == dv.overview(app._deliv_live_tabs())["new"] and dv.overview(app._deliv_live_tabs())["candidates_total"] == 1)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
