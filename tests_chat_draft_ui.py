#!/usr/bin/env python3
"""The chat view when the terminal holds a draft, and when a queued send is given up (#35):
web/index.html in a browser with a stand-in Python side.

Reported in daily use: a message sent from the chat view said it was queued and never went
in, because of text left in the terminal's input box, which the chat view does not show.
  - the server says the box holds a draft: a card shows its first words with "clear it and
    send" and "not now"; nothing is sent, and the message stays in the box
  - "not now": the card goes, the message stays
  - "clear it and send": sent again with clear_draft, queued (the tab is working): the bubble
    says why it waits
  - the server gives it up: the bubble says "not sent" and why, with "send again" and
    "remove", and stays until one is pressed (before, it went away after 30 minutes)
  - "send again" sends it again
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_draft_ui.py
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
    print("SKIP  tests_chat_draft_ui.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "好，我先看一下筆記資料夾。"}]
STUB = """
window.__calls = []; window.__draft = true; window.__queue = {queued: [], given_up: []};
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
      case 'chat_send': {
        window.__calls.push({text: args[1], clear: !!args[2]});
        if (!args[2] && window.__draft)               // the terminal still holds it
          return R({success: false, code: 'draft', draft: '終端機裡的草稿（右鍵誤貼上來的）'});
        if (args[2]) { window.__draft = false; window.__queue = {queued: ['q1'], given_up: []};
                       return R({success: true, queued: true, id: 'q1', reason: 'working', position: 1}); }
        return R({success: true, queued: false});
      }
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (CFG, SESSIONS, TURNS))
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1000, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1200)

    print(f"[{engine}] 1. a draft in the terminal")
    page.fill("#chat-input", "這週的筆記也幫我分類")
    page.press("#chat-input", "Enter")
    page.wait_for_selector(".cm-draft-card", timeout=5000)
    card = page.inner_text(".cm-draft-card")
    check("a card with the draft's words, and the two choices",
          "終端機裡有一段還沒送出的字" in card and "右鍵誤貼上來的" in card and "清掉後送出" in card and "先不送" in card, card)
    check("…nothing waits as sent, and the message stays in the box",
          not page.query_selector("#chat-list .is-pending") and page.input_value("#chat-input") == "這週的筆記也幫我分類")
    if engine == "webkit":
        page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "chat-draft-card.png"))
    page.click("[data-act='draft-keep']")
    page.wait_for_timeout(200)
    check("not now: the card goes, the message stays",
          not page.query_selector(".cm-draft-card") and page.input_value("#chat-input") == "這週的筆記也幫我分類")

    print(f"[{engine}] 2. clear it and send")
    page.press("#chat-input", "Enter")
    page.wait_for_selector(".cm-draft-card", timeout=5000)
    page.click("[data-act='draft-clear']")
    page.wait_for_timeout(400)
    calls = page.evaluate("window.__calls")
    check("sent again, asking for the clear", calls[-1] == {"text": "這週的筆記也幫我分類", "clear": True}, calls)
    pend = page.inner_text("#chat-list .is-pending")
    check("…queued: the bubble says why it waits", "等它做完這一步就送進去" in pend and "這週的筆記也幫我分類" in pend, pend)

    print(f"[{engine}] 3. given up")
    page.evaluate("window.__queue = {queued: [], given_up: [{id: 'q1', why: '它正在跑', outcome: 'working'}]}")
    page.wait_for_selector("#chat-list .is-failed", timeout=8000)
    failed_txt = page.inner_text("#chat-list .is-failed")
    check("the bubble says it was not sent, and why, with send again and remove",
          "沒送出：它正在跑" in failed_txt and "重送" in failed_txt and "移除" in failed_txt, failed_txt)
    page.wait_for_timeout(2500)
    check("…and stays (it does not go away by itself)", bool(page.query_selector("#chat-list .is-failed")))
    if engine == "webkit":
        page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "chat-send-given-up.png"))
    page.click("#chat-list .is-failed [data-act='resend']")
    page.wait_for_timeout(400)
    calls = page.evaluate("window.__calls")
    check("send again sends it again", calls[-1] == {"text": "這週的筆記也幫我分類", "clear": False} and
          not page.query_selector("#chat-list .is-failed"), calls)
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
