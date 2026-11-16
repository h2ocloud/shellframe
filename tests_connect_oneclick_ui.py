#!/usr/bin/env python3
"""One click to connect a service through the Claude account's connector: web/connect.js in a headless browser
talking to the real Python side (api_connect's js_api, the engine, the shared connector adapter) for a made-up
service, with a stand-in for the temporary test tab. Nothing opens a browser, a tab or a program.

  1. ShellBot can tell Claude is signed in with a claude.ai account: "Connect" opens the service's own page on
     claude.ai at once (no card to choose a way, no sign-in card); the card says what to do there
  2. coming back to ShellBot (the window gets the focus) finishes that step and runs the test by itself; the
     test passes and the catalog counts it as connected
  3. ShellBot cannot tell: the card asks, says why (the check's own words), and "Yes" opens the page
  4. the test finds the connector not authorized: it points back at step 2, which does not open the page again
     by itself, and offers to open it again
WebKit (the Mac app's engine), headless. Made-up service only. Screenshots: qa-shots/connect-catalog-oneclick-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_connect_oneclick_ui.py
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
    print("SKIP  tests_connect_oneclick_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import api_connect  # noqa: E402
from connectors import clis, engine as eng_mod, registry  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.services._claude_connector import ConnectorAdapter  # noqa: E402

T = fake.T
URL = "https://example.com/directory/00000000-0000-4000-8000-000000000001"


def TT(en, zh):
    return {"en": en, "zh": zh}


DESC = {
    "schema": 1, "id": "demo_files", "name": TT("Demo Files", "示範檔案櫃"), "icon": "folder",
    "summary": TT("Let your bots find demo files", "讓 bot 找示範檔案"), "recommended": True,
    "category": {"id": "productivity", "name": TT("Productivity", "生產力")},
    "scopes": [{"id": "account", "label": TT("What you allow", "你允許的事"), "required": True}],
    "routes": [{
        "id": "connector", "kind": "claude_connector", "name": TT("Use your Claude account's connector", "用 Claude 帳號的連接器"),
        "recommended": True, "minutes": 2, "scopes": ["account"], "clis": {"claude": {"via": "connector"}},
        "steps": [
            {"id": "a1", "title": TT("Is Claude signed in with a claude.ai account?", "Claude 是用 claude.ai 帳號登入的嗎？"),
             "how": [TT("ShellBot could not tell. With a Claude subscription it is: press Yes.", "ShellBot 看不出來。用的是 Claude 訂閱就是：按「是」。")],
             "done_label": TT("Yes, a claude.ai account", "是，用 claude.ai 帳號"),
             "check": {"id": "claude_account", "on": "enter", "pass": ["claude_ai"], "states": {
                 "claude_ai": {"tone": "good", "text": TT("Signed in with a claude.ai account ({plan})", "用 claude.ai 帳號登入（{plan}）")},
                 "unknown": {"tone": "warn", "text": TT("ShellBot cannot tell how Claude is signed in.", "ShellBot 看不出 Claude 是怎麼登入的。"),
                             "hint": TT("Type /status in a Claude tab.", "在 Claude 分頁打 /status。")}}}},
            {"id": "a2", "title": TT("Connect Demo Files on the demo page", "在示範頁面連結 示範檔案櫃"),
             "how": [TT("Your browser opened the demo page. Press Connect there.", "瀏覽器已打開示範頁面。在那裡按「連結」。"),
                     TT("Come back to ShellBot: it tests the connection by itself.", "回到 ShellBot：它會自己測試通不通。")],
             "url": URL, "in_browser": True, "redo_on_reconnect": True},
            {"id": "a3", "kind": "test", "title": TT("Test that it works", "測試通不通"), "intro": TT("A temporary tab counts demo files.", "臨時分頁數一下示範檔案。")}],
        "test": {"via": TT("Demo Files connector", "示範檔案櫃連接器"), "verdicts": {
            "needs_auth": {"text": TT("The connector is not authorized yet.", "連接器還沒授權好。"), "next": TT("Go back to step 2.", "回到第 2 步。")}}}}],
}
SIGNED_IN = {"accounts": {"global": {"claude": "p1"}, "profiles": {"claude": [{"id": "p1", "plan": "max"}]}}}


class Kit:
    """The temporary test tab, as connect_tabs.TabKit: what it was asked, and what its transcript says (set by the test)."""

    def __init__(self):
        self.asked, self.snap, self.alive = [], {}, set()

    def command(self, cli="claude"):
        return cli

    def open(self, cmd, title):
        sid = f"s{900 + len(self.alive)}"
        self.alive.add(sid)
        return sid

    def ask(self, sid, text, ready_timeout=90.0):
        self.asked.append((sid, text))

    def phase(self, sid):
        return "asking" if sid in self.alive else "closed"

    def read(self, sid):
        return dict(self.snap) if sid in self.alive else {"alive": False}

    def close(self, sid):
        self.alive.discard(sid)


class Api(api_connect.ConnectApiMixin):
    """The real js_api over a real engine, for the made-up service only."""

    def __init__(self, cfg):
        self.sessions = {}
        self.kit = Kit()
        self.adapter = ConnectorAdapter("demo_files", tool="Demo_Files", mark="DEMO-TEST", name=("Demo Files", "示範檔案櫃"))
        reg = registry.Registry(dirs=[])
        assert reg.register(json.loads(json.dumps(DESC)), adapter=self.adapter), reg.errors
        self._connect_engine = eng_mod.Engine(
            registry=reg, path=os.path.join(tempfile.mkdtemp(prefix="connect-oneclick-"), "connectors.json"),
            clis=clis.Clis(runner=fake.FakeRunner(), finder=fake.finder()), backend=fake.backend(), platform="mac",
            ctx_factory=lambda s, r: Ctx(service=s, route=r, cfg=cfg, tabs=self.kit))

    def pass_test(self, ok=True, auth_only=False):
        """What the test tab's transcript says: the question went in, a tool was called, the marker line."""
        sid, q = self.kit.asked[-1]
        tool = "mcp__claude_ai_Demo_Files__" + ("authenticate" if auth_only else "search")
        line = "DEMO-TEST: OK" if ok else "DEMO-TEST: FAIL not authorized"
        self.kit.snap = {"alive": True, "state": "", "events": [{"kind": "user_msg", "text": q}, {"kind": "tool_call", "tool": tool},
                                                                 {"kind": "assistant_text", "text": "3\n" + line}, {"kind": "turn_end"}]}


STUB = r"""
window.__calls = []; window.__opened = [];
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    if (String(prop).startsWith('connect_')) return window.__py(String(prop), JSON.stringify(args));
    const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
    switch (prop) {
      case 'get_config': return R({settings: {language: 'zh-TW', sidebar_open: true}, presets: [{name: 'Claude', cmd: 'claude', icon: 'C'}], agent_roster: {}});
      case 'list_sessions': return R([{sid: 's501', cmd: 'claude', alive: true, label: '示範分頁'}]);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'open_url': window.__opened.push(args[0]); return R('ok');
      default: return R('{}');
    }
  };
}})};
"""


def open_page(browser, api):
    page = browser.new_page(viewport={"width": 1100, "height": 820}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.expose_function("__py", lambda name, args: getattr(api, name)(*json.loads(args)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("window.SFConnect && SFConnect.list().loaded", timeout=15000)
    page.click("#btn-settings")
    page.click('#settings-tabs-bar [data-tab="connect"]')
    page.wait_for_selector('#settings-panel-connect .cg-row[data-service="demo_files"]', timeout=5000)
    return page, errors


def come_back(page):
    """The person returns from the browser: the window gets the focus (well after the page was opened)."""
    page.evaluate("SFConnect.state().away = Date.now() - 5000; window.dispatchEvent(new Event('focus'))")


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#cn-card").screenshot(path=str(SHOTS / name))


def run(browser):
    print("1. signed in with a claude.ai account: one click opens the service's page")
    api = Api(SIGNED_IN)
    page, errors = open_page(browser, api)
    page.click('#settings-panel-connect .cg-row[data-service="demo_files"] button')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("the service's own page opened at once, through the app", page.evaluate("window.__opened") == [URL], page.evaluate("window.__opened"))
    check("the card is at step 2 (the sign-in was known, so no card asked about it), with no way to choose",
          "在示範頁面連結 示範檔案櫃" in page.inner_text("#cn-card .cn-title") and "第 2 步，共 3 步" in page.inner_text("#cn-card .cn-prog-label")
          and page.locator("#cn-card .cn-route").count() == 0)
    check("…it says what to do there, offers to open it again, and its button tests",
          "瀏覽器已打開示範頁面" in page.inner_text("#cn-card") and page.inner_text('#cn-card [data-act="open"]').startswith("再打開一次")
          and page.inner_text('#cn-card [data-act="did"]') == "連好了，測試")
    st = api._connect().store.get("demo_files")["steps"]["a1"]["check"]
    check("…the sign-in check passed with the plan it found", st == {"code": "claude_ai", "passed": True, "fields": {"plan": "Max"}}, st)
    shot(page, "connect-catalog-oneclick-zh.png")
    page.wait_for_timeout(200)
    come_back(page)
    page.wait_for_function("document.querySelector('#cn-card .cn-spin')", timeout=5000)
    check("coming back finished the step and started the test by itself (one question to a temporary tab)",
          len(api.kit.asked) == 1 and "DEMO-TEST" in api.kit.asked[0][1], api.kit.asked)
    api.pass_test()
    page.wait_for_selector("#cn-card .cn-result.ok", timeout=8000)
    check("the test passed: connected", "已連結" in page.inner_text("#cn-card .cn-result") and api._connect().status("demo_files")["state"] == "connected")
    shot(page, "connect-catalog-oneclick-done-zh.png")
    page.click('#cn-card [data-act="finish"]')
    page.wait_for_function("document.querySelector('#settings-panel-connect .cg-count').textContent.includes('1')", timeout=5000)
    check("the catalog counts it, and its button says so", page.inner_text("#settings-panel-connect .cg-count") == "已連結 1 個 ›"
          and page.inner_text('#settings-panel-connect .cg-row[data-service="demo_files"] button') == "✓ 已連結")
    check("one click in all: Connect, then the page, then back (no other button was pressed)",
          [c[0] for c in page.evaluate("window.__calls") if c[0] in ("connect_start", "connect_step")] == ["connect_start", "connect_step"])
    check("no page errors", not errors, errors)
    page.close()

    print("2. ShellBot cannot tell how Claude is signed in: it asks")
    api = Api({})
    page, errors = open_page(browser, api)
    page.click('#settings-panel-connect .cg-row[data-service="demo_files"] button')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("the card asks, with the check's own words and what to do", "Claude 是用 claude.ai 帳號登入的嗎？" in page.inner_text("#cn-card .cn-title")
          and "ShellBot 看不出 Claude 是怎麼登入的" in page.inner_text("#cn-card .cn-msg") and "在 Claude 分頁打 /status" in page.inner_text("#cn-card .cn-msg"),
          page.inner_text("#cn-card"))
    check("…its button is the answer, and nothing opened yet", page.inner_text('#cn-card [data-act="did"]') == "是，用 claude.ai 帳號"
          and page.evaluate("window.__opened") == [])
    shot(page, "connect-catalog-ask-login-zh.png")
    page.click('#cn-card [data-act="did"]')
    page.wait_for_function("window.__opened.length === 1", timeout=5000)
    check("Yes goes on to step 2 and opens the page", page.evaluate("window.__opened") == [URL] and "第 2 步" in page.inner_text("#cn-card .cn-prog-label"))
    check("no page errors", not errors, errors)
    page.close()

    print("3. the connector is not authorized yet")
    api = Api(SIGNED_IN)
    page, errors = open_page(browser, api)
    page.click('#settings-panel-connect .cg-row[data-service="demo_files"] button')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    page.wait_for_timeout(200)
    page.click('#cn-card [data-act="did"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-spin')", timeout=5000)
    api.pass_test(ok=False, auth_only=True)
    page.wait_for_selector("#cn-card .cn-result.bad", timeout=8000)
    check("the result says so and points at step 2", "連接器還沒授權好" in page.inner_text("#cn-card .cn-result") and "第 2 步" in page.inner_text("#cn-card .cn-result"))
    n0 = page.evaluate("window.__calls.length")
    page.click('#cn-card button[data-act="goto"]:text-matches("回到第 2 步")')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    page.wait_for_timeout(300)
    went = [c for c in page.evaluate("window.__calls")[n0:] if c[0] == "connect_step"]
    check("Back to step 2 lands on step 2 without a step back (the failed test had already moved it there)",
          "第 2 步，共 3 步" in page.inner_text("#cn-card .cn-prog-label") and went == [], (page.inner_text("#cn-card .cn-prog-label"), went))
    check("back at step 2 the page is not opened again by itself; the button opens it again",
          page.evaluate("window.__opened") == [URL] and "再打開一次" in page.inner_text('#cn-card [data-act="open"]'))
    page.click('#cn-card [data-act="open"]')
    check("…and it does", page.evaluate("window.__opened") == [URL, URL])
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
