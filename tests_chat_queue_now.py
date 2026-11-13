#!/usr/bin/env python3
"""A chat send waiting behind the tab's turn says what holds it and offers "send now":
web/index.html in a browser with a stand-in Python side.

Reported in daily use: the bubble and the line under the box said only "waiting for the message
from Telegram to be taken" while the tab spent minutes on a message from Telegram; it looked stuck.
  - the bubble says who is still on what, where that came from and for how long, and that this
    one goes in right after (chat_view's queue.busy: reason and since when the turn runs)
  - "send now" sits on the bubble with what it does (it may interrupt the tab's work); pressing it
    asks chat_send_now for that send, and the bubble goes on as sent
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_queue_now.py
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
    print("SKIP  tests_chat_queue_now.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "好，我先看一下筆記資料夾。"}]
STUB = """
window.__calls = []; window.__queue = {queued: [], given_up: []};
window.__cfg = %s; window.__sessions = %s; window.__turns = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R({success: true, readable: true, provider: 'claude', activity: 'working',
                                  decision: null, rev: 'r' + JSON.stringify(window.__queue), turns: window.__turns,
                                  queue: window.__queue});
      case 'chat_send':            // the tab is on a message from Telegram, 4 minutes in
        window.__calls.push(['chat_send', args[1]]);
        window.__queue = {queued: ['q1'], given_up: [],
                          busy: {reason: 'telegram', since: Math.floor(Date.now() / 1000) - 245}};
        return R({success: true, queued: true, id: 'q1', reason: 'telegram', position: 1});
      case 'chat_send_now':
        window.__calls.push(['chat_send_now', args[0], args[1]]);
        window.__queue = {queued: [], given_up: []};
        return R({success: true, message: 'Sent'});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, TURNS))
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
SAYS = "示範分頁還在處理上一則（從 Telegram 來的，已經 4 分鐘），處理完會接著送。"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1000, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1200)

    print(f"[{engine}] 1. queued behind a message from Telegram")
    page.fill("#chat-input", "這週的筆記也幫我分類")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-list .is-pending [data-act='send-now']", timeout=8000)
    pend = page.inner_text("#chat-list .is-pending")
    check("the bubble says who is on what, from where, how long, and that this one follows", SAYS in pend, pend)
    check("…the line under the box says the same", SAYS in page.inner_text("#chat-note") if page.query_selector("#chat-note")
          else SAYS in page.inner_text("#chat-layer"))
    check("…with 'send now' and what it does", "插隊送出" in pend and "可能打斷它手上正在做的事" in pend, pend)
    if engine == "webkit":
        page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "chat-queue-send-now.png"))

    print(f"[{engine}] 2. send now")
    page.click("#chat-list .is-pending [data-act='send-now']")
    page.wait_for_timeout(500)
    calls = page.evaluate("window.__calls")
    check("chat_send_now for that send", calls[-1] == ["chat_send_now", "s501", "q1"], calls)
    pend = page.inner_text("#chat-list .is-pending") if page.query_selector("#chat-list .is-pending") else ""
    check("…the bubble goes on as sent: no wait words, no button",
          SAYS not in pend and not page.query_selector("#chat-list [data-act='send-now']"), pend)
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:                       # webkit is optional: `playwright install webkit`
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
