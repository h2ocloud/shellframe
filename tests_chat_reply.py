#!/usr/bin/env python3
"""A right-click on a message in the chat view offers Reply, which quotes it into the box.

Asked for: in the chat view, a right-click on a message should let the person reply to it.
  - on the agent's answer: Reply, then the box starts with "> <the bot>：" and the message's lines as "> "
  - on another agent's message: the quote names that agent
  - on empty space: the menu has Paste only
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_reply.py
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
    print("SKIP  tests_chat_reply.py（沒裝 playwright）")
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
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1200)
    items = lambda: page.locator(".sf-menu .sf-menu-item").all_inner_texts()

    print(f"[{engine}] 1. the agent's answer")
    page.locator("#chat-list .cm-row.cm-bot .cm-bubble").first.click(button="right")
    page.wait_for_selector(".sf-menu", timeout=3000)
    check("the menu offers Reply and Paste", any("回覆" in x for x in items()) and any("貼上" in x for x in items()), items())
    page.locator(".sf-menu .sf-menu-item", has_text="回覆").click()
    page.wait_for_timeout(200)
    box = page.input_value("#chat-input")
    check("the box starts with the bot's name and the message as > lines, the cursor after them",
          box.startswith("> 示範分頁：\n> 好，我先看一下筆記資料夾。\n> 分成三類：會議、待辦、想法。\n\n")
          and page.evaluate("document.activeElement.id") == "chat-input", repr(box))
    if engine == "webkit":
        page.locator("#chat-layer").screenshot(path=str(HERE / "qa-shots" / "chat-reply.png"))

    print(f"[{engine}] 2. another agent's message")
    page.fill("#chat-input", "")
    page.locator("#chat-list .cm-row.cm-peer .cm-bubble").first.click(button="right")
    page.wait_for_selector(".sf-menu", timeout=3000)
    page.locator(".sf-menu .sf-menu-item", has_text="回覆").click()
    page.wait_for_timeout(200)
    check("the quote names that agent", page.input_value("#chat-input").startswith("> 研究：\n> 比較表整理好了"),
          repr(page.input_value("#chat-input")))

    print(f"[{engine}] 3. empty space")
    page.fill("#chat-input", "")
    page.locator("#chat-list").click(button="right", position={"x": 5, "y": 5})
    page.wait_for_timeout(300)
    check("Paste only", items() and not any("回覆" in x for x in items()), items())
    page.keyboard.press("Escape")
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
