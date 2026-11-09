#!/usr/bin/env python3
"""Bots (experimental_bots): identity, memory in and out, the primary bot, and
bots that live outside ShellFrame.

What is guarded here, end to end against a temp config and a local webhook:

  - the face tokens in web/bots.js and bots.py are the same set
  - a bot wakes with its 執掌, skills, MEMORY.md and last summary, once
  - a sleeping bot reached through delegate/A2A gets its memory in the same message
  - closing a bot tab makes the bot write its own summary first; a bot that does
    not answer still leaves a file (the screen), and the tab is closed either way
  - a bot's tab closed from sfctl/Telegram/idle (close_session) takes the same path
  - the webhook URL and auth never reach the page
  - A2A to an external bot POSTs its webhook and never opens a placeholder tab
    (the failure this feature was built to fix); its answer, sent with
    `sfctl a2a-result --as`, lands in the tab that asked
  - an external bot's hand-off result is POSTed back to it
  - `--as` only works outside a tab and only as an external roster role

Run: .venv/bin/python tests_bots.py
"""
import http.server
import json
import os
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"   # this file checks the Chinese wording; tests_prompt_lang.py covers English
import re
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bots-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
sys.modules["webview"] = MagicMock()
bt = MagicMock()
bt.load_user_instructions.return_value = ""
sys.modules["bridge_telegram"] = bt
sys.path.insert(0, str(HERE))

import agent_link  # noqa: E402
agent_link.STATE_DIR = TMP / "state"
agent_link.LOG_FILE = agent_link.STATE_DIR / "agent_link.json"
agent_link.STATE_DIR.mkdir(parents=True, exist_ok=True)

import a2a_host  # noqa: E402
import api_bots  # noqa: E402
import bots  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
api_bots.SUMMARY_WAIT_SEC = 3
api_bots.BUSY_WAIT_SEC = 2

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


# ── a webhook that records what it was sent ────────────────────────────────
HITS = []


class Hook(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        HITS.append({"path": self.path, "auth": self.headers.get("Authorization"),
                     "body": json.loads(self.rfile.read(n) or b"{}")})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Hook)
threading.Thread(target=srv.serve_forever, daemon=True).start()
HOOK_URL = f"http://127.0.0.1:{srv.server_address[1]}/routines/secret-path-123/trigger"
HOOK_AUTH = "Bearer sk-test-very-secret"


# ── a fake app ─────────────────────────────────────────────────────────────
class FakeSession:
    def __init__(self, sid, cmd, label=""):
        self.sid, self.cmd, self._custom_label = sid, cmd, label
        self.alive = True
        self.lock = threading.Lock()
        self._recent = bytearray(b"last screen line\n")
        self._tmux_name = None
        self._startup_trust_pending = True
        self._init_pending = True


def fresh_config(extra_roster=None):
    roster = {
        "Coding": {"label": "Coding-CLD", "cmd": "claude", "responsibility": "程式與測試",
                   "handoff": True,
                   "bot": {"avatar": {"shape": "pill", "color": "red"}, "skills": ["demo-skill"],
                           "memory": True}},
        "研究": {"label": "研究-CLD", "cmd": "codex", "responsibility": "資料調研", "handoff": True},
        "總管": {"label": "總管-外部", "cmd": "zsh -lc 'echo placeholder; exec zsh -l'",
                 "responsibility": "只派工與驗收", "handoff": False, "coordinator": True,
                 "bot": {"avatar": {"shape": "star", "color": "purple"}, "primary": True,
                         "external": {"kind": "webhook", "url": HOOK_URL, "auth": HOOK_AUTH}}},
    }
    roster.update(extra_roster or {})
    cfg = {"settings": {"experimental_bots": True, "experimental_a2a": True},
           "agent_roster": roster, "presets": []}
    main.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


def make_api():
    api = Api.__new__(Api)
    api.sessions = {}
    api.bridge = api.line_bridge = None
    api._window = None
    api.sent = []        # (sid, text)
    api.closed = []      # (sid, reason)
    counter = [100]

    def new_session(cmd, cols, rows, source="manual", handoff=False, inherit_accounts=True):
        counter[0] += 1
        sid = f"s{counter[0]}"
        api.sessions[sid] = FakeSession(sid, cmd)
        return sid

    def rename_session(sid, name, manual=False):
        api.sessions[sid]._custom_label = name
        return json.dumps({"success": True})

    def send(s, text, submit=False):
        api.sent.append((s.sid, text))
        m = re.search(r"寫成新檔 (\S+\.md)", text)
        if m and getattr(s, "writes_memory", True):        # the bot does what it is asked
            p = Path(m.group(1))
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 摘要\n- 做了 X\n", encoding="utf-8")
            mm = re.search(r"更新長期記憶 (\S+MEMORY\.md)", text)
            if mm:
                Path(mm.group(1)).write_text("- 使用者偏好：繁體中文\n", encoding="utf-8")
        return True

    def close_session(sid, reason="manual", handoff=False, summary_path="", idle_seconds=None):
        api.closed.append((sid, reason))
        api.sessions.pop(sid, None)

    api.new_session = new_session
    api.rename_session = rename_session
    api._send_text_to_session = send
    api.close_session = close_session
    api._wait_until_ready = lambda sid, timeout=45.0: sid in api.sessions
    api._agent_state_for_list = lambda sid: getattr(api.sessions.get(sid), "state", "")
    return api


def reset_a2a():
    agent_link._recent.clear()
    agent_link._inbound_depth.clear()
    agent_link._seen_ids.clear()
    agent_link._fingerprints.clear()
    for p in (agent_link.LOG_FILE, agent_link.handoff_path()):
        try:
            p.unlink()
        except OSError:
            pass


def wait_for(cond, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ── 1. tokens ──────────────────────────────────────────────────────────────
print("tokens")
js = (HERE / "web" / "bots.js").read_text(encoding="utf-8")
shapes_js = re.findall(r"^\s{4}(\w+):\s+\{ name:", js, re.M)
palette_js = re.findall(r"(\w+): '#[0-9A-Fa-f]{6}'", js.split("const PALETTE")[1].split("};")[0])
check("bots.js shapes == bots.SHAPES", tuple(shapes_js) == bots.SHAPES, f"{shapes_js}")
check("bots.js palette == bots.COLORS", tuple(palette_js) == bots.COLORS, f"{palette_js}")
css = (HERE / "web" / "bots.css").read_text(encoding="utf-8")
for cls in ("sig-working", "sig-decision", "sig-stuck", "sig-done", "on.attention", "is-asleep", "is-writing"):
    check(f"bots.css has an expression for {cls}", cls in css)

# ── 2. pure helpers ────────────────────────────────────────────────────────
print("helpers")
check("slug keeps CJK, drops path syntax", bots.slug("研究/a:b") == "研究ab")
check("slug never empty", bots.slug("///") == "bot")
check("name with / refused", bool(bots.name_error("peer/x")))
check("name with : refused", bool(bots.name_error("ext:x")))
check("reserved names refused", bool(bots.name_error("ui")) and bool(bots.name_error("worker")))
check("normal name ok", bots.name_error("研究助理") == "")
check("avatar with an unknown shape dropped", bots.normalize_avatar({"shape": "x", "color": "red"}) is None)
check("avatar with a hex colour kept", bots.normalize_avatar({"shape": "egg", "color": "#112233"}) ==
      {"shape": "egg", "color": "#112233"})
try:
    bots.normalize_external({"url": "ftp://x"})
    check("non-http webhook refused", False)
except ValueError:
    check("non-http webhook refused", True)
check("external url None keeps the stored one",
      bots.normalize_external({"url": None, "auth": None}, {"url": "https://a/b", "auth": "k"}) ==
      {"kind": "webhook", "url": "https://a/b", "auth": "k"})
pub = json.dumps(bots.public_roster({"x": {"bot": {"external": {"url": HOOK_URL, "auth": HOOK_AUTH}}}}))
check("public roster hides webhook path and auth",
      "secret-path-123" not in pub and "sk-test" not in pub and '"url_set": true' in pub)
check("masked url keeps only the host", bots.mask_url("https://u:p@h.example/x/y?z=1") == "https://h.example/***")

skills = TMP / "skills"
(skills / "demo-skill").mkdir(parents=True)
(skills / "demo-skill" / "SKILL.md").write_text(
    "---\nname: demo-skill\ndescription: does demo things\n---\n# x\n", encoding="utf-8")
cat = bots.skill_catalog([skills])
check("skill catalog reads frontmatter", cat and cat[0]["name"] == "demo-skill"
      and cat[0]["description"] == "does demo things")
check("resolve_skills gives the SKILL.md path",
      bots.resolve_skills(["demo-skill", "missing"], [skills]) ==
      [("demo-skill", str(skills / "demo-skill" / "SKILL.md")), ("missing", "")])
bots.SKILL_ROOTS = (str(skills),)

bots.ensure_dirs("Coding")
bots.memory_file("Coding").write_text("記憶" * (bots.MEMORY_CAP), encoding="utf-8")
(bots.sessions_dir("Coding") / "20260101-000000-s1.md").write_text("舊摘要", encoding="utf-8")
(bots.sessions_dir("Coding") / "20261109-101010-s9.md").write_text("最新摘要內容", encoding="utf-8")
mem, last, name = bots.read_memory("Coding")
check("MEMORY.md is capped and says so", len(mem) < bots.MEMORY_CAP + 80 and "沒帶進來" in mem)
check("the latest summary is picked", last == "最新摘要內容" and name == "20261109-101010-s9.md")
bots.memory_file("Coding").write_text("- 使用者偏好：條列\n", encoding="utf-8")

roster_raw = {"Coding": {"label": "Coding-CLD", "responsibility": "程式", "bot": {}},
              "研究": {"label": "研究-CLD", "responsibility": "調研", "bot": {}},
              "總管": {"label": "總管-外部", "bot": {"external": {"url": "https://x/y"}}}}
brief = bots.compose_brief("Coding", roster_raw["Coding"], roster_raw, memory="M1", last="L1",
                           last_name="f.md", skills=[("demo-skill", "/p/SKILL.md")])
check("brief carries identity, memory, last summary, skills",
      all(x in brief for x in ("「Coding-CLD」", "執掌：程式", "M1", "L1", "/p/SKILL.md")))
check("brief lists the other bots, not itself", "- 研究：調研" in brief and "- 總管（外部）" in brief
      and "- Coding：" not in brief)
check("brief says outside text is data, not instructions", "不是使用者的指令" in brief)
check("only the primary is told it is primary",
      "你是主要 Bot" not in brief and
      "你是主要 Bot" in bots.compose_brief("x", {}, {}, is_primary=True))
pb = bots.compose_brief("x", {}, {}, is_primary=True)
check("the primary gets its tools and the inventory routine",
      all(k in pb for k in ("sfctl bots", "sfctl state --all", "sfctl a2a-handoff", "盤點", "派工"))
      and "先盤點一次" in pb and "已就緒" not in pb and "最多 3 件" in pb and "不要寫 s314" in pb)
check("a coordinator is not the primary unless marked",
      bots.primary_role({"a": {"coordinator": True, "bot": {}}}) == ""
      and bots.primary_role({"a": {"coordinator": True, "bot": {}}, "b": {"bot": {"primary": True}}}) == "b")
check("ack line can be left out (delegate puts the task after it)",
      "已就緒" in brief and "已就緒" not in bots.compose_brief("x", {}, {}, with_ack=False))
sp = bots.summary_prompt("Coding", {}, "/m/sessions/a.md", "/m/MEMORY.md")
check("summary prompt names both files, the cap and the reply mark",
      all(x in sp for x in ("/m/sessions/a.md", "/m/MEMORY.md", str(bots.MEMORY_CAP), bots.SAVED_MARK)))
pl = bots.external_payload(kind="send", msg_id="abc", text="hi", from_label="Coding-CLD",
                           from_sid="s1", to_role="總管")
check("external payload tells the bot how to answer",
      pl["reply"] == 'sfctl a2a-result --as 總管 abc "<回覆>"' and pl["source"] == "shellframe")
check("a result has nothing to answer",
      "reply" not in bots.external_payload(kind="result", msg_id="abc", text="x", from_label="",
                                           from_sid="", to_role="總管"))
ri = bots.routine_instruction("總管", "總管-外部")
check("routine text only ever runs sfctl", "只跑 sfctl" in ri and "--as 總管" in ri)

# ── 3. state for the page ──────────────────────────────────────────────────
print("state")
fresh_config()
api = make_api()
st = json.loads(api.bots_state())
check("enabled and primary come from config", st["enabled"] is True and st["primary"] == "總管")
check("page state hides the webhook", "secret-path-123" not in json.dumps(st) and "sk-test" not in json.dumps(st))
api.sessions["s1"] = FakeSession("s1", "claude", "隨便一個分頁")
r = json.loads(api.bots_set_avatar("s1", json.dumps({"shape": "ghost", "color": "teal"})))
check("a plain tab keeps its own avatar", r["success"] and
      json.loads(api.bots_state())["sessions"]["s1"]["avatar"] == {"shape": "ghost", "color": "teal"})
api.bots_set_avatar("s1", "null")
check("reset drops the override", "s1" not in json.loads(api.bots_state())["sessions"])

# ── 4. make a tab a bot ────────────────────────────────────────────────────
print("bot mode")
r = json.loads(api.bots_save("寫手", json.dumps({"responsibility": "寫文件", "avatar": {"shape": "drop", "color": "sky"},
                                                  "skills": ["demo-skill"], "memory": True}), "s1"))
cfg = json.loads(main.CONFIG_FILE.read_text())
check("a tab becomes a bot", r["success"] and cfg["agent_roster"]["寫手"]["bot"]["created_by_bots"] is True)
check("the tab now carries the bot's name", api.sessions["s1"]._custom_label == "寫手")
check("the tab is bound to the bot", cfg["bots_sessions"]["s1"]["bot"] == "寫手")
check("the memory folder exists", bots.bot_dir("寫手").is_dir())
r = json.loads(api.bots_save("bad/name", "{}", ""))
check("an A2A-unsafe name is refused", not r["success"])
r = json.loads(api.bots_save("寫手", json.dumps({"primary": True}), ""))
cfg = json.loads(main.CONFIG_FILE.read_text())
check("one primary at a time", cfg["agent_roster"]["寫手"]["bot"]["primary"] is True
      and cfg["agent_roster"]["總管"]["bot"]["primary"] is False)
api.bots_save("總管", json.dumps({"primary": True, "external": {"kind": "webhook", "url": None, "auth": None}}), "")
cfg = json.loads(main.CONFIG_FILE.read_text())
check("saving without the secrets keeps them", cfg["agent_roster"]["總管"]["bot"]["external"]["url"] == HOOK_URL
      and cfg["agent_roster"]["總管"]["bot"]["primary"] is True)

api.sessions["s3"] = FakeSession("s3", "claude", "Coding-CLD")
check("a tab named like a bot is that bot's tab", api._bots_role_of("s3") == "Coding")
r = json.loads(api.bots_bind_tab("s3", ""))
check("its own settings can make it a plain tab again, even with the bot's name",
      r["success"] and api._bots_role_of("s3") == "" and json.loads(api.bots_state())["sessions"]["s3"].get("plain"))
r = json.loads(api.bots_bind_tab("s3", "Coding"))
check("…and a bot's tab once more", r["success"] and api._bots_role_of("s3") == "Coding"
      and not json.loads(api.bots_state())["sessions"]["s3"].get("plain"))
r = json.loads(api.bots_bind_tab("s3", "總管"))
check("a tab cannot be an external bot", not r["success"])
r = json.loads(api.bots_set_primary("Coding"))
cfg = json.loads(main.CONFIG_FILE.read_text())
check("set_primary moves the card to another bot", r["success"] and bots.primary_role(cfg["agent_roster"]) == "Coding"
      and cfg["agent_roster"]["總管"]["bot"]["primary"] is False)
r = json.loads(api.bots_set_primary(""))
check("set_primary('') empties the card", r["success"] and bots.primary_role(json.loads(main.CONFIG_FILE.read_text())["agent_roster"]) == "")
r = json.loads(api.bots_set_primary("研究"))
check("only a bot can be primary", not r["success"])

# ── 5. wake ────────────────────────────────────────────────────────────────
print("wake")
fresh_config()
api = make_api()
r = json.loads(api.bots_open("Coding"))
sid = r["details"]["sid"]
check("waking opens a tab named after the bot", api.sessions[sid]._custom_label == "Coding-CLD")
check("the brief arrives once the tab is ready",
      wait_for(lambda: any(s == sid and "叫醒" in t for s, t in api.sent)))
text = [t for s, t in api.sent if s == sid][0]
check("brief has MEMORY.md, the last summary and the skill path",
      "使用者偏好：條列" in text and "最新摘要內容" in text and "demo-skill" in text and "SKILL.md" in text)
check("the generic init prompt is not sent on top", api.sessions[sid]._init_pending is False)
r = json.loads(api.bots_open("Coding"))
check("waking an awake bot just finds its tab", r["details"].get("existed") and r["details"]["sid"] == sid)
api._agent_status_snapshot = lambda s: ({"state": "working", "action": "Editing relay.py", "narration": "先改 timeout"}, 0)
api._agent_state_for_list = lambda s: "working"
lst = api._bots_list_for_sfctl()
row = [b for b in lst["details"]["bots"] if b["role"] == "Coding"][0]
check("sfctl bots tells the primary what each awake bot is doing",
      row["state"] == "awake" and row["doing"] == "working" and row["latest"] == "Editing relay.py"
      and "最新：Editing relay.py" in lst["message"], lst["message"])
api._agent_state_for_list = lambda s: getattr(api.sessions.get(s), "state", "")
r = json.loads(api.bots_open("總管"))
check("an external bot cannot be woken here", not r["success"])

# ── 6. close with memory ───────────────────────────────────────────────────
print("close")
before = set(bots.sessions_dir("Coding").glob("*.md"))
r = json.loads(api.bots_close(sid, "save"))
check("close starts the memory flow", r["success"])
check("the tab closes after the bot wrote its summary",
      wait_for(lambda: (sid, "bot-memory") in api.closed))
new = set(bots.sessions_dir("Coding").glob("*.md")) - before
check("one new summary file, written by the bot", len(new) == 1 and "做了 X" in list(new)[0].read_text())
check("long-term memory updated", "繁體中文" in bots.memory_file("Coding").read_text())
check("nothing left marked as closing", not api_bots._CLOSING)

api2 = make_api()
s2 = FakeSession("s7", "claude", "Coding-CLD")
s2.writes_memory = False
api2.sessions["s7"] = s2
before = set(bots.sessions_dir("Coding").glob("*.md"))
api2.bots_close("s7", "save")
check("a bot that never writes still gets closed", wait_for(lambda: ("s7", "bot-memory") in api2.closed, 12))
new = set(bots.sessions_dir("Coding").glob("*.md")) - before
check("…and leaves the captured screen instead", len(new) == 1 and "自動擷取" in list(new)[0].read_text()
      and "last screen line" in list(new)[0].read_text())

api3 = make_api()
api3.sessions["s8"] = FakeSession("s8", "claude", "Coding-CLD")
api3.sessions["s8"].state = "working"
api3.bots_close("s8", "save")
time.sleep(0.6)
check("a working bot is not interrupted", not any("請先寫記憶" in t for _, t in api3.sent))
api3.sessions["s8"].state = "done"
check("…it is asked once the turn ends", wait_for(lambda: ("s8", "bot-memory") in api3.closed, 10))

api4 = make_api()
api4.sessions["s9"] = FakeSession("s9", "claude", "Coding-CLD")
api4.bots_close("s9", "skip")
check("skip closes right away without a prompt", ("s9", "bot-skip") in api4.closed and not api4.sent)

api5 = make_api()
api5.sessions["s10"] = FakeSession("s10", "claude", "Coding-CLD")
api5.sessions["s11"] = FakeSession("s11", "claude", "一般分頁")
check("close_session of a bot tab from elsewhere is diverted", api5._bots_divert_close("s10", "tg") is True)
check("…a plain tab is not", api5._bots_divert_close("s11", "tg") is False)
check("…and the flow's own close is never diverted", api5._bots_divert_close("s10", "bot-memory") is False)
wait_for(lambda: ("s10", "bot-memory") in api5.closed, 10)
src = (HERE / "main.py").read_text(encoding="utf-8")
body = src.split("    def close_session(", 1)[1].split("\n    def ", 1)[0]
check("close_session asks the bots hook before tearing anything down",
      body.find("_bots_divert_close") != -1 and body.find("_bots_divert_close") < body.find("self.sessions.pop"))

# ── 7. delegate / A2A to a sleeping bot ────────────────────────────────────
print("sleeping bot")
fresh_config()
api = make_api()
res = api.delegate_task("Coding", "請修 relay timeout")
sid = res["details"]["sid"]
first = [t for s, t in api.sent if s == sid][0]
check("delegate to a sleeping bot sends brief + task in one message",
      "叫醒" in first and "請修 relay timeout" in first and first.index("叫醒") < first.index("請修 relay"))
check("…and binds the tab", json.loads(main.CONFIG_FILE.read_text())["bots_sessions"][sid]["bot"] == "Coding")
res = api.delegate_task("研究", "查資料")
first = [t for s, t in api.sent if s == res["details"]["sid"]][0]
check("a plain worker role gets no brief", "叫醒" not in first)
n = len(api.sessions)
res = api.delegate_task("總管", "幫我排程")
check("delegate to an external bot is refused, no placeholder tab", not res["success"] and len(api.sessions) == n)

# ── 8. external bots over A2A ──────────────────────────────────────────────
print("external")
reset_a2a()
HITS.clear()
fresh_config()
api = make_api()
api.sessions["s1"] = FakeSession("s1", "claude", "Coding-CLD")
n = len(api.sessions)
r = a2a_host.handle(api, "a2a_send", {"target": "總管", "text": "relay 修好了", "from_sid": "s1"})
check("A2A to the external bot succeeds", r.get("success"), r)
check("…without opening any tab (the old bug opened a placeholder)", len(api.sessions) == n)
check("…by POSTing its webhook with the auth header", len(HITS) == 1 and HITS[0]["auth"] == HOOK_AUTH)
body = HITS[0]["body"] if HITS else {}
check("…a body it can act on", body.get("kind") == "send" and body.get("text") == "relay 修好了"
      and body.get("from") == "Coding-CLD" and body.get("reply", "").startswith("sfctl a2a-result --as 總管 "))
mid = body.get("id", "")
r = a2a_host.handle(api, "a2a_result", {"id": mid, "text": "收到，下一步做 Y", "as": "總管", "from_sid": ""})
check("its answer (sfctl a2a-result --as) succeeds", r.get("success"), r)
check("…and lands in the tab that asked, labelled as the external bot",
      any(s == "s1" and "總管-外部" in t and "下一步做 Y" in t for s, t in api.sent))
r = a2a_host.handle(api, "a2a_send", {"target": "Coding", "text": "冒充", "as": "總管", "from_sid": "s1"})
check("--as inside a tab is refused", not r.get("success") and "分頁外面" in r.get("message", ""))
r = a2a_host.handle(api, "a2a_send", {"target": "Coding", "text": "冒充", "as": "Coding", "from_sid": ""})
check("--as a local bot is refused", not r.get("success"))

HITS.clear()
r = a2a_host.handle(api, "a2a_handoff", {"target": "Coding", "text": "跑一次測試", "as": "總管", "from_sid": ""})
check("the external bot can hand work to a local bot", r.get("success"), r)
hid = (r.get("details") or {}).get("id", "")
check("…delivered into that bot's tab", any(s == "s1" and "跑一次測試" in t for s, t in api.sent))
r = a2a_host.handle(api, "a2a_result", {"id": hid, "text": "測試全綠", "from_sid": "s1"})
check("the local bot's result is POSTed back to the external bot",
      r.get("success") and HITS and HITS[-1]["body"].get("kind") == "result"
      and HITS[-1]["body"].get("text") == "測試全綠", r)

HITS.clear()
fresh_config({"總管": {"label": "總管-外部", "cmd": "zsh", "coordinator": True,
                       "bot": {"external": {"kind": "webhook", "url": "", "auth": ""}}}})
reset_a2a()
r = a2a_host.handle(api, "a2a_send", {"target": "總管", "text": "在嗎", "from_sid": "s1"})
check("no webhook yet: a clear refusal, not a stuck tab",
      not r.get("success") and "webhook" in r.get("message", "") and len(api.sessions) == n)

# ── 9. the local HTTP API speaks the same A2A ──────────────────────────────
print("http /a2a")
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import api_server  # noqa: E402

fresh_config()
reset_a2a()
api = make_api()
api.sessions["s1"] = FakeSession("s1", "claude", "Coding-CLD")
httpd, _t = api_server.start(lambda cmd, args: a2a_host.handle(api, cmd, args),
                             host="127.0.0.1", port=0, token="tok-123")
base = f"http://127.0.0.1:{httpd.server_address[1]}"


def post(path, body, token="tok-123"):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


code, _ = post("/a2a", {"kind": "send", "as": "總管", "target": "Coding", "text": "hi"}, token="")
check("POST /a2a without the token is refused", code == 401)
code, _ = post("/a2a", {"kind": "send", "target": "Coding", "text": "hi"})
check("…without `as` it is a 400", code == 400)
code, res = post("/a2a", {"kind": "send", "as": "總管", "target": "Coding", "text": "從 webhook 來的"})
check("…with both it delivers like sfctl --as", code == 200 and res.get("success")
      and any(s == "s1" and "從 webhook 來的" in t and "總管-外部" in t for s, t in api.sent), res)
code, res = post("/a2a", {"kind": "send", "as": "Coding", "target": "研究", "text": "冒充"})
check("…and `as` a local bot is refused", code == 200 and not res.get("success"))
httpd.shutdown()

# ── 10. index.html stays thin ──────────────────────────────────────────────
print("index.html")
html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
check("bots.js and bots.css are loaded", 'src="bots.js"' in html and 'href="bots.css"' in html)
check("every local dot hosts a face", html.count("SFBots.face(sid)") == 3)
check("closing a tab asks the bots hook first", html.count("SFBots.interceptClose(sid)") == 2)
check("the facade is the only coupling", "window.__sfHost = {" in html)
called = set(re.findall(r"api\(\)\.([A-Za-z_]\w*)\(", js)) | set(re.findall(r"\ba\.([A-Za-z_]\w*)\(", js))
missing = sorted(n for n in called if not hasattr(Api, n))
check(f"every backend call bots.js makes exists ({len(called)})", called and not missing, missing)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
srv.shutdown()
sys.exit(1 if failed else 0)
