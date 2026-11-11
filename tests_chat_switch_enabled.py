#!/usr/bin/env python3
"""The Chat button of a tab whose command the page cannot read: web/index.html in a browser
with a stand-in Python side.

Reported in daily use: with the terminal style on, a tab started as
`env -u CLAUDE_CONFIG_DIR claude --resume …` had its Chat button greyed for good. The page
read the command as "-u", and in the terminal style it never asked the server.
  - that tab: the button turns on once the server says claude, and it shows the conversation
  - a shell tab stays a terminal: the button stays greyed
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_switch_enabled.py
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
    print("SKIP  tests_chat_switch_enabled.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s601", "cmd": "env -u CLAUDE_CONFIG_DIR claude --resume 0e1f", "alive": True,
             "label": "示範分頁", "bridge_enabled": True},
            {"sid": "s602", "cmd": "zsh", "alive": True, "label": "終端機", "bridge_enabled": True}]
VIEWS = {"s601": {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None,
                  "rev": "r1", "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                                         {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]},
         "s602": {"success": True, "readable": False, "why": "provider", "provider": "other", "activity": "",
                  "decision": None, "rev": "r2", "turns": []}}
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__views = %s; window.__active = 's601';
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: window.__active});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__views[args[0]] || {success: false});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEWS, ensure_ascii=False))

CHAT_BTN = '#chat-switch button[data-mode="chat"]'
ENABLED = "(() => { const b = document.querySelector('%s'); return !!b && !b.disabled; })()" % CHAT_BTN
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!document.querySelector('%s')" % CHAT_BTN, timeout=15000)

    print("1. env -u … claude, terminal style")
    try:
        page.wait_for_function(ENABLED, timeout=5000)
    except Exception:
        pass
    check("the Chat button turns on once the server says claude", page.evaluate(ENABLED),
          page.evaluate("window.__calls.filter(c => c[0] === 'chat_view').map(c => c[1][0])"))
    check("…the terminal still shows: the style is not switched for you", not page.evaluate(SHOWN))
    if page.evaluate(ENABLED):
        page.locator(CHAT_BTN).click()
    try:
        page.wait_for_function(SHOWN, timeout=5000)
    except Exception:
        pass
    check("pressing it shows the conversation", page.evaluate(SHOWN))
    check("…with its turns", "有什麼要做的" in (page.text_content("#chat-layer") or ""))

    print("2. a shell tab")
    page.evaluate("window.__active = 's602'; window.__sfHost.switchTab('s602')")
    page.wait_for_timeout(800)
    asked = page.evaluate("window.__calls.filter(c => c[0] === 'chat_view' && c[1][0] === 's602').length")
    check("the server is asked once", asked == 1, asked)
    check("the Chat button stays greyed", not page.evaluate(ENABLED))
    check("no page errors", not errors, errors)
    page.screenshot(path=str(HERE / "qa-shots" / "chat-switch-enabled.png"))
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
