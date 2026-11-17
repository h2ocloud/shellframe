#!/usr/bin/env python3
"""Plaud's sign-in in the wizard card: web/connect.js in a headless browser talking to the real Python side (api_connect's
js_api, the engine, plaud.json and the Plaud adapter as shipped) with a FAKE stdio MCP server in place of Plaud's, and a
stand-in for the temporary test tab. No network, no browser of the person's, no real Plaud, no token.

  1. Connect: Node.js passes by itself, "Add it for me" adds the server, "Sign in to Plaud" starts the sign-in and the
     card says it waits for the browser ON THIS COMPUTER; the card follows it by itself and moves on when it ends ok;
     the test then passes
  2. the sign-in times out: the card says so, stops asking, and the same button tries again (which then works)
  3. the port is busy: the card says to wait and try again
WebKit (the Mac app's engine), headless. Screenshots: qa-shots/connect-plaud-*.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_connect_plaud_ui.py
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
    print("SKIP  tests_connect_plaud_ui.py（沒裝 playwright）")
    sys.exit(0)

sys.modules.setdefault("webview", MagicMock())
import api_host  # noqa: E402

api_host.bind({"load_config": lambda: {}, "update_config": lambda fn: None,
               "ACCOUNT_MANAGER": type("AM", (), {"session_refs": lambda s, cfg: {}, "env_for": lambda s, p, r: {}})()})
import _connectors_fake as fake  # noqa: E402
import _plaud_fake  # noqa: E402
import api_connect  # noqa: E402
from connectors import clis, engine as eng_mod, registry  # noqa: E402
from connectors.adapter import Ctx  # noqa: E402
from connectors.clis import Result  # noqa: E402
from connectors.services import plaud  # noqa: E402

PRE = "mcp__shellbot-plaud__"
NODE, NPX = "/fake/bin/node", "/fake/bin/npx"


class Kit:
    """The temporary test tab, as connect_tabs.TabKit."""

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
    """The real js_api over a real engine, the shipped descriptions, a temp home folder."""

    def __init__(self):
        self.sessions = {}
        self.kit = Kit()
        self.home = pathlib.Path(tempfile.mkdtemp(prefix="plaud-ui-", dir=os.environ.get("SHELLFRAME_TEST_ROOT") or None))
        self._connect_engine = eng_mod.Engine(
            registry=registry.Registry(), path=str(self.home / "connectors.json"),
            clis=clis.Clis(runner=fake.FakeRunner(), finder=fake.finder()), backend=fake.backend(), platform="mac", ctx_factory=self.ctx)

    def ctx(self, service, route):
        c = Ctx(service=service, route=route, cfg={}, env={"HOME": str(self.home), "PATH": "/nowhere"}, tabs=self.kit)
        c.find = lambda b: {"node": NODE, "npx": NPX}.get(b)
        c.run = lambda argv, timeout=40, env=None: Result(0, "v20.11.1") if list(argv) == [NODE, "--version"] else Result(1, "")
        return c

    def pass_test(self):
        sid, q = self.kit.asked[-1]
        self.kit.snap = {"alive": True, "state": "", "events": [{"kind": "user_msg", "text": q}, {"kind": "tool_call", "tool": PRE + "list_files"},
                                                                 {"kind": "assistant_text", "text": "1\nPLAUD-TEST: OK"}, {"kind": "turn_end"}]}


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
    page.wait_for_selector('#settings-panel-connect .cg-row[data-service="plaud"]', timeout=5000)
    return page, errors


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.locator("#cn-card").screenshot(path=str(SHOTS / name))


def title(page):
    return page.inner_text("#cn-card .cn-title")


def steps_called(page, action):
    return [c for c in page.evaluate("window.__calls") if c[0] == "connect_step" and c[1][1] == "act" and json.loads(c[1][2]).get("action") == action]


def to_signin(page):
    page.click('#settings-panel-connect .cg-row[data-service="plaud"] button')
    page.wait_for_selector("#cn-card .cn-how", timeout=5000)
    check("Connect opens the add step directly (one way; Node.js 20 passed by itself)", "把伺服器加進主 bot 用的 AI" in title(page)
          and "第 2 步，共 4 步" in page.inner_text("#cn-card .cn-prog-label"), page.inner_text("#cn-card"))
    page.click('#cn-card [data-act="do"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('登入 Plaud')", timeout=5000)


def run(browser):
    print("1. sign in, the card follows it, the test passes")
    plaud.SPAWN, plaud.LOGIN_CAP_S = _plaud_fake.Spawner("ok", delay=3.5), 30
    api = Api()
    page, errors = open_page(browser, api)
    to_signin(page)
    text = page.inner_text("#cn-card")
    check("the sign-in step says the browser opens ON THIS COMPUTER and has the two buttons", "這台電腦" in text and "登入 Plaud" in page.inner_text('#cn-card [data-act="do"]')
          and page.locator('#cn-card [data-act="do"]').count() == 2, text)
    shot(page, "connect-plaud-signin-zh.png")
    page.click('#cn-card [data-act="do"] >> nth=0')
    page.wait_for_function("document.querySelector('#cn-card .cn-msg') && document.querySelector('#cn-card .cn-msg').textContent.includes('在這台電腦的瀏覽器打開')", timeout=5000)
    check("pressing it answers at once: it is waiting in the browser on this computer, and the step stays", "登入 Plaud" in title(page)
          and "第 3 步" in page.inner_text("#cn-card .cn-prog-label") and api._connect().store.get("plaud")["step"] == "p2")
    check("the card shows the waiting state", "在這台電腦的瀏覽器打開" in page.inner_text("#cn-card .cn-msg"), page.inner_text("#cn-card"))
    shot(page, "connect-plaud-waiting-zh.png")
    n0 = len(steps_called(page, "login_wait"))
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('測試接通')", timeout=20000)
    check("the card asked again by itself (quietly) and moved on to the test when the sign-in ended",
          len(steps_called(page, "login_wait")) > n0 and len(steps_called(page, "login")) == 1 and "第 4 步" in page.inner_text("#cn-card .cn-prog-label"),
          (n0, len(steps_called(page, "login_wait"))))
    shot(page, "connect-plaud-test-zh.png")
    page.click('#cn-card [data-act="test-start"]')
    page.wait_for_function("document.querySelector('#cn-card .cn-spin')", timeout=5000)
    check("the test asks one fixed question in a temporary tab, and never for a title", len(api.kit.asked) == 1 and "PLAUD-TEST" in api.kit.asked[0][1]
          and "list_files" in api.kit.asked[0][1] and "不要重複任何標題" in api.kit.asked[0][1], api.kit.asked)
    api.pass_test()
    page.wait_for_selector("#cn-card .cn-result.ok", timeout=8000)
    check("connected", "已連結" in page.inner_text("#cn-card .cn-result") and api._connect().status("plaud")["state"] == "connected")
    shot(page, "connect-plaud-done-zh.png")
    check("no page errors", not errors, errors)
    page.close()

    print("2. the sign-in times out; the same button tries again")
    plaud.SPAWN, plaud.LOGIN_CAP_S = _plaud_fake.Spawner("timeout", delay=0.5), 30
    api = Api()
    page, errors = open_page(browser, api)
    to_signin(page)
    page.click('#cn-card [data-act="do"] >> nth=0')
    page.wait_for_function("document.querySelector('#cn-card .cn-msg') && document.querySelector('#cn-card .cn-msg').textContent.includes('兩分鐘內沒有人完成登入')", timeout=15000)
    check("the card says nobody finished in two minutes, and stays on the sign-in step", "登入 Plaud" in title(page) and "第 3 步" in page.inner_text("#cn-card .cn-prog-label"))
    shot(page, "connect-plaud-timeout-zh.png")
    n = len(steps_called(page, "login_wait"))
    page.wait_for_timeout(4000)
    check("it stopped asking once the result stopped waiting", len(steps_called(page, "login_wait")) == n, (n, len(steps_called(page, "login_wait"))))
    check("the buttons work again (nothing stays disabled)", page.locator("#cn-card button[disabled]").count() == 0)
    plaud.SPAWN = _plaud_fake.Spawner("ok", delay=0.5)
    page.click('#cn-card [data-act="do"] >> nth=0')
    page.wait_for_function("document.querySelector('#cn-card .cn-title').textContent.includes('測試接通')", timeout=20000)
    check("pressing it again signs in and the card moves on", len(steps_called(page, "login")) == 2)
    check("no page errors", not errors, errors)
    page.close()

    print("3. the port is busy")
    plaud.SPAWN = _plaud_fake.Spawner("port_busy", delay=0.2)
    api = Api()
    page, errors = open_page(browser, api)
    to_signin(page)
    page.click('#cn-card [data-act="do"] >> nth=0')
    page.wait_for_function("document.querySelector('#cn-card .cn-msg') && document.querySelector('#cn-card .cn-msg').textContent.includes('另一個 Plaud 登入')", timeout=15000)
    check("the card says another sign-in is running", "另一個 Plaud 登入" in page.inner_text("#cn-card .cn-msg"))
    shot(page, "connect-plaud-portbusy-zh.png")
    page.click('#cn-card [data-act="stuck"]')
    page.wait_for_selector("#cn-card .cn-stuck", timeout=3000)
    check("\"I'm stuck\" lists what people hit here, including the browser opening on this computer",
          "瀏覽器沒有打開" in page.inner_text("#cn-card .cn-stuck") and "還有另一個登入在跑" in page.inner_text("#cn-card .cn-stuck")
          and "我人不在這台電腦前" in page.inner_text("#cn-card .cn-stuck"))
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as p:
    browser = p.webkit.launch(headless=True)
    try:
        run(browser)
    finally:
        plaud.LOGIN.stop()
        browser.close()

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
