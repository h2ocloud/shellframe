#!/usr/bin/env python3
"""A question card in the chat view can be put away (✕), and comes back for the next question.

Reported: the "it is waiting for you in the terminal" card could not be closed after the question was
answered in the terminal (the tab's state was late to change).
  - ✕ puts the card away; it stays away while the same question is up
  - once the tab is no longer waiting, the next wait shows the card again
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_ask_dismiss.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
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
    print("SKIP  tests_chat_ask_dismiss.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": False, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
import agent_link  # noqa: E402
TURNS = [{"kind": "user_msg", "ts": 1794190841, "text": "幫我整理這週的筆記"},
         {"kind": "assistant_text", "ts": 1794190843, "text": "好，我先看一下筆記資料夾。\n分成三類：會議、待辦、想法。"},
         {"kind": "user_msg", "ts": 1794190850, "text": agent_link.format_delivery("研究", "比較表整理好了", lang="zh"),
          "from": {"kind": "a2a", "name": "研究", "handoff": False}}]
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
      case 'chat_view': return R({success: true, readable: true, provider: 'claude', activity: window.__act || 'decision',
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
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_selector("#chat-ask .cm-ask-card", timeout=8000)
    check("the waiting card shows, with a ✕", page.locator("#chat-ask .cm-ask-x").count() == 1)
    page.click("#chat-ask .cm-ask-x")
    page.wait_for_timeout(1500)
    check("✕ puts it away, and it stays away while the tab still waits", page.locator("#chat-ask .cm-ask-card").count() == 0)
    page.evaluate("window.__act = 'working'")
    page.wait_for_timeout(1500)
    page.evaluate("window.__act = 'decision'")
    page.wait_for_selector("#chat-ask .cm-ask-card", timeout=8000)
    check("…the next wait shows the card again", page.locator("#chat-ask .cm-ask-card").count() == 1)
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
