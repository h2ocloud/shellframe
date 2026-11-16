#!/usr/bin/env python3
"""The chat view's sign-in card (web/tablogin.js) in the real page, with a stand-in Python side.

Asked for: lead someone who knows neither the product nor the CLI through signing in again, buttons only.
  - expired: "Sign in again" and one button; it starts the sign-in (chat_login start)
  - the menu: the subscription first (most people), the API account second
  - the browser: "open the sign-in page" opens its URL here; a code from the page is pasted back
  - done: Continue; then it offers to send the last message again, and does
Run in Chromium, and in WebKit when playwright has it. Demo data only. Screenshots: qa-shots/tab-login-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_tab_login_ui.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_tab_login_ui.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s801", "cmd": "claude", "alive": True, "label": "示範分頁"}]
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "Login expired · Please run /login"}]
URL = "https://claude.ai/oauth/authorize?code=true&client_id=demo&state=demo"
STUB = """
window.__calls = []; window.__step = 'expired';
window.__cfg = %s; window.__sessions = %s; window.__turns = %s;
const NEXT = {start: 'menu', subscription: 'browser', code: 'done', finish: ''};
const login = () => window.__step ? Object.assign({step: window.__step}, window.__step === 'browser' ? {url: %s} : {}) : {};
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's801'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'preset_groups': return R([]);
      case 'chat_view': return R({success: true, readable: true, provider: 'claude', activity: 'done', decision: null,
                                  rev: 'r' + window.__step + window.__calls.length, turns: window.__turns, login: login(),
                                  queue: {queued: [], given_up: []}});
      case 'chat_login': window.__calls.push(['chat_login', args[1], args[2] || '']);
                         if (args[1] in NEXT) window.__step = NEXT[args[1]];
                         return R({success: true, login: login()});
      case 'chat_send': window.__calls.push(['chat_send', args[1]]); return R({success: true});
      case 'open_url': window.__calls.push(['open_url', args[0]]); return R({success: true});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(TURNS, ensure_ascii=False),
       json.dumps(URL))
CARD = ".cm-login-card"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1000, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector(CARD, timeout=20000)
    calls = lambda: page.evaluate("window.__calls")
    shot = lambda n: page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / f"tab-login-{n}.png")) if engine == "chromium" else None

    print(f"[{engine}] expired")
    check("the card says to sign in again, no command to type", "要重新登入" in page.inner_text(CARD)
          and "不用打任何指令" in page.inner_text(CARD), page.inner_text(CARD))
    shot("1-expired")
    page.click(f"{CARD} [data-act='login-start']")
    page.wait_for_selector(f"{CARD}[data-step='menu']", timeout=6000)
    check("its button starts the sign-in", ["chat_login", "start", ""] in calls(), calls())

    print(f"[{engine}] the menu")
    txt = page.inner_text(CARD)
    check("the subscription first, the API account second", txt.index("Claude 訂閱帳號") < txt.index("API 帳號"), txt)
    shot("2-menu")
    page.click(f"{CARD} [data-act='login-subscription']")
    page.wait_for_selector(f"{CARD}[data-step='browser']", timeout=6000)

    print(f"[{engine}] the browser")
    shot("3-browser")
    page.click(f"{CARD} [data-act='login-open']")
    page.wait_for_timeout(200)
    check("'open the sign-in page' opens its URL on this computer", ["open_url", URL] in calls(), calls())
    page.click(f"{CARD} [data-act='login-code']")
    page.wait_for_timeout(200)
    check("no code typed: nothing sent", not any(c[1] == "code" for c in calls() if c[0] == "chat_login"))
    page.fill(f"{CARD} .cm-login-code", "demo-code-123")
    page.click(f"{CARD} [data-act='login-code']")
    page.wait_for_selector(f"{CARD}[data-step='done']", timeout=6000)
    check("the code is pasted back", ["chat_login", "code", "demo-code-123"] in calls(), calls())

    print(f"[{engine}] done, and the last message again")
    page.click(f"{CARD} [data-act='login-finish']")
    page.wait_for_selector(f"{CARD}[data-step='after']", timeout=6000)
    check("Continue presses its Enter", ["chat_login", "finish", ""] in calls(), calls())
    check("…then it offers the last message again, quoted", "要把剛才那則再送一次嗎" in page.inner_text(CARD)
          and "幫我整理這週的筆記" in page.inner_text(CARD), page.inner_text(CARD))
    shot("4-after")
    page.click(f"{CARD} [data-act='login-resend']")
    page.wait_for_timeout(400)
    check("…and sends it", ["chat_send", "幫我整理這週的筆記"] in calls(), calls())
    check("…and the card is gone", not page.query_selector(CARD))
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            run(browser, engine)
        finally:
            browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
