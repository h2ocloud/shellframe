#!/usr/bin/env python3
"""Settings → Connect recognizes connections that already work (web/connect.js over the real js_api, engine and
connectors/detect.py), for made-up services, with a stand-in for every AI program whose `claude mcp list` and
`codex mcp list` answers are scripted here. Nothing opens a browser, a tab or a program.

  1. opening the tab starts a look in the background; the page asks again until it is done, and says it counts
     connections already set up on this computer
  2. a connected row says through which way: "已連結 · Claude 帳號連接器", "已連結 · 本機 MCP（你自己的設定）"; a way
     found but not working is one short line on the row, which still offers Connect; "N connected" counts them
  3. a service whose own setup stopped at step 2 is connected through the way that works; its card lists every way
     with what was found (✅ checked and in use / 🟡 found in Codex, not tested) and where ShellBot's own steps are,
     with "Continue it", which goes on at that step
  4. the person's own setting: the card says ShellBot uses it as it is and never removes it; there is nothing to
     disconnect; clearing ShellBot's half-done setup elsewhere never touches the person's entries
  5. "Look again": a connector authorized meanwhile becomes connected
  6. the choice of a way shows what was found for each; a card ShellBot's own steps connected lists the ways too
  7. English words; no page errors
WebKit (the Mac app's engine), headless. Made-up names only. Screenshots: qa-shots/connect-detect-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_connect_detect_ui.py
"""
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
sys.path.insert(0, str(HERE))
os.environ.setdefault("SHELLFRAME_TESTS", "1")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_connect_detect_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
from connectors import clis, engine as eng_mod, registry  # noqa: E402
from connectors.adapter import Adapter, Ctx  # noqa: E402

eng_mod._dlog = lambda c, m: None
api_connect._dlog = lambda c, m: None


def TT(en, zh):
    return {"en": en, "zh": zh}


PROD = {"id": "productivity", "name": TT("Productivity", "生產力")}
SCOPES = [{"id": "read", "label": TT("Read", "讀取"), "required": True}]
ALL = ("claude", "codex", "agy", "grok", "opencode", "cursor", "pi")


def connector_route(name):
    return {"id": "connector", "kind": "claude_connector", "name": TT("Use your Claude account's connector", "用 Claude 帳號的連接器"),
            "blurb": TT("Connect once on the demo site.", "在示範網站連結一次。"), "recommended": True, "minutes": 2, "scopes": ["read"],
            "detect": {"connector": name}, "clis": {"claude": {"via": "connector"}},
            "steps": [{"id": "a1", "title": TT("Connect it on the demo site", "在示範網站連結"), "how": [TT("Press Connect there.", "在那裡按連結。")]},
                      {"id": "a2", "kind": "test", "title": TT("Test it", "測試"), "intro": TT("A demo test.", "示範測試。")}]}


def local_route(server, package, recommended=False):
    return {"id": "local", "kind": "local_mcp", "name": TT("Use a demo server on this computer (any AI)", "用本機的示範伺服器（每種 AI 都能用）"),
            "blurb": TT("A demo server.", "示範伺服器。"), "recommended": recommended, "minutes": 10, "scopes": ["read"],
            "detect": {"packages": [package]}, "clis": {c: {"via": "mcp"} for c in ALL},
            "mcp": {"server": server, "command": "npx", "args": ["-y", package + "@1.0.0"]},
            "steps": [{"id": "b1", "title": TT("Get the demo app", "準備示範程式"), "how": [TT("Install it.", "裝好它。")]},
                      {"id": "b2", "title": TT("Make a demo key file", "做一個示範金鑰檔"), "how": [TT("Save it.", "存起來。")]},
                      {"id": "b3", "kind": "test", "title": TT("Test it", "測試"), "intro": TT("A demo test.", "示範測試。")}]}


def service(sid, en, zh, icon, routes):
    return {"schema": 1, "id": sid, "name": TT(en, zh), "icon": icon, "summary": TT(f"Let your bots use {en}", f"讓 bot 用{zh}"),
            "category": PROD, "recommended": True, "scopes": SCOPES, "routes": routes}


DESCS = [
    service("demo_files", "Demo Files", "示範檔案櫃", "folder", [connector_route("Demo Files")]),
    service("demo_notes", "Demo Notes", "示範筆記", "note", [local_route("shellbot-demo-notes", "@demo/notes-mcp")]),
    service("demo_board", "Demo Board", "示範看板", "checklist", [connector_route("Demo Board")]),
    service("demo_mail", "Demo Mail", "示範信箱", "mail", [local_route("shellbot-demo-mail", "@demo/mail-mcp", recommended=True), connector_route("Demo Mail")]),
    service("demo_chat", "Demo Chat", "示範聊天室", "chat", [local_route("shellbot-demo-chat", "@demo/chat-mcp", recommended=True), connector_route("Demo Chat")]),
    service("demo_photos", "Demo Photos", "示範相簿", "plug", [connector_route("Demo Photos")]),
]
CLAUDE = ["Checking MCP server health…", "",
          "claude.ai Demo Files: https://files.example/mcp - ✔ Connected",
          "claude.ai Demo Board: https://board.example/mcp - ! Needs authentication",
          "claude.ai Demo Mail: https://mail.example/mcp - ✔ Connected",
          "claude.ai Demo Chat: https://chat.example/mcp - ! Needs authentication",
          "my-notes: npx -y @demo/notes-mcp - ✔ Connected",
          "unrelated: npx -y @demo/other-mcp - ✘ Failed to connect — demo"]
# `codex mcp list` as it prints (its plain table; env values masked)
CODEX = ("Name       Command  Args              Env         Cwd  Status   Auth       \n"
         "mail-tool  npx      -y @demo/mail-mcp  DEMO=*****  -    enabled  Unsupported\n"
         "chat-tool  npx      -y @demo/chat-mcp  DEMO=*****  -    enabled  Unsupported\n")


class Quiet(Adapter):
    """No checks, no tests are run here: this test is about what is found, not about a wizard's test."""


class Api(api_connect.ConnectApiMixin):
    """The real js_api over a real engine, for the made-up services only."""

    def __init__(self):
        self.sessions = {}
        self.runner = fake.FakeRunner()
        self.runner.listing["claude"] = "\n".join(CLAUDE)
        self.runner.listing["codex"] = CODEX
        self.runner.entries["claude"] = {"my-notes", "unrelated"}
        reg = registry.Registry(dirs=[])
        for d in DESCS:
            assert reg.register(json.loads(json.dumps(d)), adapter=Quiet()), reg.errors
        self._connect_engine = eng_mod.Engine(
            registry=reg, path=os.path.join(tempfile.mkdtemp(prefix="connect-detect-ui-"), "connectors.json"),
            clis=clis.Clis(runner=self.runner, finder=fake.finder()), backend=fake.backend(), platform="mac",
            ctx_factory=lambda s, r: Ctx(service=s, route=r, cfg={}))
        eng = self._connect_engine
        eng.start("demo_mail", route="local")
        eng.step("demo_mail", "done")                     # the person once stopped at step 2 of the local setup

        def done(x):                                      # connected through ShellBot's own steps, its test passed
            x.update(route="connector", state="connected", step="a2", steps={"a1": {"state": "done"}, "a2": {"state": "done"}},
                     clis={}, secrets={}, scopes=["read"], connected_at=1794190841, checked_at=1794190841, problem=None)
        eng.store.update("demo_photos", done)


STUB = r"""
window.__calls = [];
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (String(prop).startsWith('connect_')) return window.__py(String(prop), JSON.stringify(args));
    const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
    switch (prop) {
      case 'get_config': return R({settings: {language: %(lang)s, sidebar_open: true}, presets: [{name: 'Claude', cmd: 'claude', icon: 'C'}], agent_roster: {}});
      case 'list_sessions': return R([{sid: 's501', cmd: 'claude', alive: true, label: 'demo'}]);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      default: return R('{}');
    }
  };
}})};
"""


def open_page(browser, api, lang="zh-TW"):
    page = browser.new_page(viewport={"width": 1100, "height": 860}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.expose_function("__py", lambda name, args: getattr(api, name)(*json.loads(args)))
    page.add_init_script(STUB % {"lang": json.dumps(lang)})
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector('#settings-panel-connect .cg-row[data-service="demo_files"]', timeout=5000)
    return page, errors


def row(page, sid):
    return page.locator(f'#settings-panel-connect .cg-row[data-service="{sid}"]')


def shot_panel(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#modal-settings .modal").screenshot(path=str(SHOTS / name))


def shot_card(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#cn-card").screenshot(path=str(SHOTS / name))


def wait_connected(page, sid, timeout=12000):
    page.wait_for_function(f"(SFConnect.list().by['{sid}'] || {{}}).state === 'connected' && "
                           f"document.querySelector('#settings-panel-connect .cg-row[data-service=\"{sid}\"] .cn-main span').textContent.includes('·')",
                           timeout=timeout)


def run(browser):
    print("1-2. the catalog: a look in the background, then each row says through which way")
    api = Api()
    page, errors = open_page(browser, api)
    calls = [c for c in page.evaluate("window.__calls") if c[0] == "connect_list"]
    check("opening the tab asks connect_list with detect (the look runs in the background)", any(c[1] and json.loads(c[1][0]).get("detect") for c in calls), calls)
    wait_connected(page, "demo_files")
    wait_connected(page, "demo_notes")
    page.wait_for_function("!document.querySelector('#settings-panel-connect .cg-detect').hidden && "
                           "document.querySelector('#settings-panel-connect .cg-detect span').textContent.includes('也算在內')", timeout=12000)
    check("the page says connections already set up on this computer count, and offers to look again",
          "這台電腦上已經設好的連線也算在內" in page.inner_text("#settings-panel-connect .cg-detect")
          and page.inner_text('#settings-panel-connect [data-cg="detect"]') == "重新找", page.inner_text("#settings-panel-connect .cg-detect"))
    files = row(page, "demo_files").locator(".cn-main span").inner_text()
    notes = row(page, "demo_notes").locator(".cn-main span").inner_text()
    check("a connector connected on the account: 「已連結 · Claude 帳號連接器」", files == "已連結 · Claude 帳號連接器", files)
    check("the person's own local server: 「已連結 · 本機 MCP（你自己的設定）」", notes == "已連結 · 本機 MCP（你自己的設定）", notes)
    check("…and their buttons say connected", row(page, "demo_files").locator("button").inner_text() == "✓ 已連結"
          and row(page, "demo_notes").locator("button").inner_text() == "✓ 已連結")
    board = row(page, "demo_board").inner_text()
    check("a connector that needs authorizing: one line on the row, the button still connects, no 'needs you'",
          "⚠ 找到但連不上：Claude 帳號連接器" in board and row(page, "demo_board").locator("button").inner_text() == "連結"
          and "要處理" not in board, board)
    mail = row(page, "demo_mail").locator(".cn-main span").inner_text()
    check("the service whose own setup stopped at step 2 is connected through the way that works", mail == "已連結 · Claude 帳號連接器", mail)
    check("「已連結 4 個 ›」 counts what works (one connected by ShellBot's own steps, three found)",
          page.inner_text("#settings-panel-connect .cg-count") == "已連結 4 個 ›", page.inner_text("#settings-panel-connect .cg-count"))
    shot_panel(page, "connect-detect-catalog-zh.png")

    print("3. the card lists every way; the unfinished one is another way, with Continue")
    row(page, "demo_mail").locator("button").click()
    page.wait_for_selector("#cn-card .cn-protos", timeout=5000)
    card = page.inner_text("#cn-card")
    check("the card: connected, through the Claude account connector, found on this computer (not set up in ShellBot)",
          "已連結" in page.inner_text("#cn-card .cn-result") and "Claude 帳號連接器" in page.inner_text("#cn-card .cn-result")
          and "在這台電腦上找到的" in card and "只有 Claude 分頁用得到" in card, card)
    conn = page.locator('#cn-card .cn-proto[data-route="connector"]')
    loc = page.locator('#cn-card .cn-proto[data-route="local"]')
    check("…the connector way: ✅ checked, in use", conn.get_attribute("data-found") == "checked" and "✅" in conn.inner_text()
          and "測過能通" in conn.inner_text() and "使用中" in conn.inner_text(), conn.inner_text())
    check("…the local way: 🟡 found in Codex (the person's own entry), not tested; ShellBot's setup at step 2 of 3",
          loc.get_attribute("data-found") == "found" and "🟡" in loc.inner_text() and "找到了，還沒測" in loc.inner_text()
          and "Codex：mail-tool（你自己的設定）" in loc.inner_text() and "ShellBot 的設定：做到第 2 步（共 3 步）" in loc.inner_text(), loc.inner_text())
    check("…the footer: clear ShellBot's setup, look again, done (no test, no reconnect on a way ShellBot did not set up)",
          [b.inner_text() for b in page.locator("#cn-card .cn-foot button").all()] == ["清掉 ShellBot 的設定", "重新找", "完成"],
          [b.inner_text() for b in page.locator("#cn-card .cn-foot button").all()])
    shot_card(page, "connect-detect-card-zh.png")
    loc.locator('[data-act="resume"]').click()
    page.wait_for_selector("#cn-card .cn-prog-label", timeout=5000)
    check("「接著做 ›」 goes on with ShellBot's own steps where they were left: step 2 of 3",
          "第 2 步，共 3 步" in page.inner_text("#cn-card .cn-prog-label") and "做一個示範金鑰檔" in page.inner_text("#cn-card .cn-title"),
          page.inner_text("#cn-card .cn-prog-label"))
    page.click('#cn-card [data-act="close"]')

    print("4. the person's own setting: used as it is, never removed")
    row(page, "demo_notes").locator("button").click()
    page.wait_for_selector("#cn-card .cn-protos", timeout=5000)
    card = page.inner_text("#cn-card")
    check("the card names the entry and where it is, and says ShellBot never changes or removes it, and it may not be read-only",
          "你自己的設定" in page.inner_text("#cn-card .cn-fyi") and "Claude Code 裡找到「my-notes」" in card and "不會刪它" in card and "不一定是唯讀" in card, card)
    check("…nothing of ShellBot's to clear: no disconnect button", page.locator('#cn-card [data-act="disconnect"]').count() == 0
          and [b.inner_text() for b in page.locator("#cn-card .cn-foot button").all()] == ["重新找", "完成"])
    shot_card(page, "connect-detect-yours-zh.png")
    page.click('#cn-card [data-act="close"]')
    row(page, "demo_mail").locator("button").click()
    page.wait_for_selector('#cn-card [data-act="disconnect"]', timeout=5000)
    n0 = len(api.runner.calls)
    page.click('#cn-card [data-act="disconnect"]')
    page.wait_for_selector(".sf-dialog-msg", timeout=5000)
    msg = page.inner_text(".sf-dialog-msg")
    check("clearing ShellBot's half-done setup says what stays: the person's own setting", "mail-tool" in msg and "不會刪它" in msg and "現在能用的照樣能用" in msg, msg)
    page.click(".sf-dialog-btns .btn-primary")
    page.wait_for_function("!document.querySelector('#modal-connect').classList.contains('open')", timeout=5000)
    page.wait_for_timeout(300)
    after = api.runner.calls[n0:]
    check("…only ShellBot's own entry was asked to go; the person's entries were never named",
          all("shellbot-demo-mail" in c for c in after if c[1:3] == ["mcp", "remove"]) and not any("mail-tool" in c or "my-notes" in c for c in api.runner.calls), after)
    check("…and the service is still connected through the way that works", api._connect().status("demo_mail")["via"]["route"] == "connector"
          and api._connect().store.get("demo_mail") == {})

    print("5. Look again")
    api.runner.listing["claude"] = "\n".join(CLAUDE).replace("claude.ai Demo Board: https://board.example/mcp - ! Needs authentication",
                                                             "claude.ai Demo Board: https://board.example/mcp - ✔ Connected")
    api._connect_detect_last -= api_connect.DETECT_FLOOR_S + 1
    page.click('#settings-panel-connect [data-cg="detect"]')
    wait_connected(page, "demo_board")
    check("a connector authorized meanwhile is connected after Look again", row(page, "demo_board").locator(".cn-main span").inner_text() == "已連結 · Claude 帳號連接器"
          and page.inner_text("#settings-panel-connect .cg-count") == "已連結 5 個 ›")

    print("6. the choice of a way, and a card ShellBot's own steps connected")
    row(page, "demo_chat").locator("button").click()
    page.wait_for_selector("#cn-card .cn-route", timeout=5000)
    loc = page.locator('#cn-card .cn-route[data-route="local"] .cn-route-found')
    con = page.locator('#cn-card .cn-route[data-route="connector"] .cn-route-found')
    check("each way's card says what was found: 🟡 the person's own entry in Codex, ⚠ the connector needs authorizing",
          loc.get_attribute("data-found") == "found" and loc.inner_text() == "🟡 找到了，還沒測（你自己的設定）"
          and con.get_attribute("data-found") == "broken" and con.inner_text() == "⚠ 找到但連不上", (loc.inner_text(), con.inner_text()))
    shot_card(page, "connect-detect-route-zh.png")
    page.click('#cn-card [data-act="close"]')
    row(page, "demo_photos").locator("button").click()
    page.wait_for_selector("#cn-card .cn-protos", timeout=5000)
    ph = page.locator('#cn-card .cn-proto[data-route="connector"]')
    check("a card ShellBot's own steps connected lists the ways too: ✅ in use, and keeps its own buttons",
          ph.get_attribute("data-found") == "checked" and "使用中" in ph.inner_text()
          and [b.inner_text() for b in page.locator("#cn-card .cn-foot button").all()] == ["中斷連結", "重新測試", "重新連結", "完成"]
          and "Claude 帳號連接器" in page.inner_text("#cn-card .cn-result"), page.inner_text("#cn-card"))
    page.click('#cn-card [data-act="close"]')
    check("no page errors", not errors, errors)
    page.close()

    print("7. English")
    api = Api()
    page, errors = open_page(browser, api, lang="en")
    wait_connected(page, "demo_notes")
    check("English row words", row(page, "demo_files").locator(".cn-main span").inner_text() == "Connected · Claude account connector"
          and row(page, "demo_notes").locator(".cn-main span").inner_text() == "Connected · Local MCP (your own setting)"
          and "⚠ Found, not working: Claude account connector" in row(page, "demo_board").inner_text())
    row(page, "demo_mail").locator("button").click()
    page.wait_for_selector("#cn-card .cn-protos", timeout=5000)
    loc = page.locator('#cn-card .cn-proto[data-route="local"]').inner_text()
    check("English card words", "Ways to connect it" in page.inner_text("#cn-card") and "Found, not tested" in loc and "step 2 of 3" in loc
          and "Continue it" in loc, loc)
    shot_card(page, "connect-detect-card-en.png")
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True)
    try:
        run(browser)
    finally:
        browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
