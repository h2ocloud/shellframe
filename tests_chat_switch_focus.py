#!/usr/bin/env python3
"""Back from the chat view to the terminal, the keys reach the terminal: web/index.html in a
browser with a stand-in Python side.

Reported in daily use: after switching a tab from the chat view back to its terminal, typing
did nothing. Measured: the switch redraws the terminal (fitTerm hides .xterm-screen for a
frame), a hidden textarea takes no focus, so term.focus() did nothing and the keys went to
the switch button; a redraw while the terminal had focus also took its focus away.
  - the switch button, when the page is still: the keys reach the CLI (write_input)
  - the switch button, while the terminal is being redrawn: the same
  - ⌘⇧M there and back while it is being redrawn: the same
  - the terminal keeps its focus through a redraw of its own
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_switch_focus.py
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
    print("SKIP  tests_chat_switch_focus.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "chat"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__view = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEW, ensure_ascii=False))

SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
HIDDEN = "(() => { const l = document.getElementById('chat-layer'); return l && l.hidden; })()"
REDRAW_THEN = ("(what) => { window.__sfHost.fitTerm('s501'); requestAnimationFrame(() => {"
               " window.__hiddenAtSwitch = getComputedStyle(document.querySelector('.xterm-helper-textarea')).visibility;"
               " if (what === 'button') document.querySelector('#chat-switch button[data-mode=\"cli\"]').click();"
               " else window.SFChat.setMode('s501', what); }); }")


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1500)                       # the sidebar's own opening redraws are over
    writes = lambda: page.evaluate("window.__calls.filter(c => c[0] === 'write_input').length")
    focused = lambda: page.evaluate("document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea')")

    def typed(text):
        before = writes()
        page.keyboard.type(text)
        page.wait_for_timeout(300)
        return writes() - before

    def back_to_chat():
        page.evaluate("window.SFChat.setMode('s501', 'chat')")
        page.wait_for_function(SHOWN)
        page.wait_for_timeout(300)

    print(f"[{engine}] 1. the switch button, the page still")
    page.locator('#chat-switch button[data-mode="cli"]').click()
    page.wait_for_function(HIDDEN)
    page.wait_for_timeout(200)
    check("the terminal has the focus", focused())
    check("the keys reach the CLI", typed("abc") == 3)

    print(f"[{engine}] 2. the switch button while the terminal is being redrawn")
    back_to_chat()
    page.evaluate(REDRAW_THEN, "button")
    page.wait_for_function(HIDDEN)
    page.wait_for_timeout(200)
    check("the switch did happen mid-redraw (the textarea was hidden)",
          page.evaluate("window.__hiddenAtSwitch") == "hidden", page.evaluate("window.__hiddenAtSwitch"))
    check("the terminal has the focus once the redraw is done", focused())
    check("the keys reach the CLI", typed("xyz") == 3)

    print(f"[{engine}] 3. ⌘⇧M back to the terminal while it is being redrawn (setMode, what the shortcut calls)")
    back_to_chat()
    page.evaluate(REDRAW_THEN, "cli")
    page.wait_for_function(HIDDEN)
    page.wait_for_timeout(200)
    check("the terminal has the focus", focused())
    check("the keys reach the CLI", typed("123") == 3)

    print(f"[{engine}] 4. a redraw of the terminal itself")
    page.evaluate("window.__sfHost.fitTerm('s501')")
    page.wait_for_timeout(200)
    check("the terminal keeps its focus", focused())
    check("the keys still reach the CLI", typed("ok") == 2)
    check("no page errors", not errors, errors)


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
