#!/usr/bin/env python3
"""Keys typed while the terminal is on screen reach the terminal, whatever kept the keyboard (web/termkeys.js).

Reported: after switching a tab from the chat view back to its terminal (the window resized around it),
nothing typed reached the CLI in the app: no write_input at all. Playwright kept the focus, so the rule
is tested where the keyboard is left on something else:
  - the keyboard on a button (the switch just clicked): the keys go to the active terminal, which takes
    the focus, and one debug line says what had the keyboard
  - after switching tabs (the sidebar, the tab bar), the new tab's terminal gets the keys
  - a click in the terminal gives it the keyboard, also when the CLI reports the mouse
  - a visible text box (the sidebar's search) keeps its keys
  - with the chat view up, nothing is taken
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_term_keys.py
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
               " if (what === 'button') document.querySelector('#chat-head .cm-to-term').click();"
               " else window.SFChat.setMode('s501', what); }); }")



def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.add_init_script("window.__sessions.push({sid: 's502', cmd: 'claude', alive: true, label: '示範二', bridge_enabled: true})")
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1500)
    writes = lambda: [c[1][1] for c in page.evaluate("window.__calls") if c[0] == "write_input"]
    rescued = lambda: [c for c in page.evaluate("window.__calls") if c[0] == "js_debug" and c[1][0] == "keys-rescued"]
    page.evaluate("window.SFChat.setStyle('cli')")
    page.evaluate("window.SFChat.setMode('s501', 'chat')")
    page.wait_for_function(SHOWN)
    page.locator('#tab-bar #chat-switch button[data-mode="cli"]').click()
    page.wait_for_function(HIDDEN)
    page.wait_for_timeout(300)

    print(f"[{engine}] 1. the keyboard left on the switch button")
    page.evaluate("document.querySelector('#tab-bar #chat-switch button[data-mode=\\\"cli\\\"]').focus()")
    before = len(writes())
    page.keyboard.type("ab")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    got = writes()[before:]
    check("the keys reach the terminal", "".join(got) == "ab\r", got)
    check("…the terminal has the focus now",
          page.evaluate("document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea')"))
    check("…and a debug line says what had the keyboard",
          len(rescued()) >= 1 and '"from":"' in rescued()[0][1][1] and '"from":null' not in rescued()[0][1][1], rescued())

    print(f"[{engine}] 2. after switching tabs, the new tab's terminal gets the keys")
    for how, sel, sid in (("the sidebar", "#sidebar-sessions [data-sid='s502']", "s502"),
                          ("the tab bar", "#tab-bar [data-sid='s501']", "s501")):
        page.locator(sel).first.click()
        page.wait_for_timeout(500)
        before = [c for c in page.evaluate("window.__calls") if c[0] == "write_input" and c[1][0] == sid]
        page.keyboard.type("ok")
        page.wait_for_timeout(300)
        after = [c for c in page.evaluate("window.__calls") if c[0] == "write_input" and c[1][0] == sid]
        check(f"switched from {how}: the keys reach {sid}", "".join(c[1][1] for c in after[len(before):]) == "ok",
              after[len(before):])

    print(f"[{engine}] 2a. a click in the terminal, even one that reports the mouse")
    page.evaluate("window._pushOutput('s501', '\\x1b[?1049h\\x1b[?1003h\\x1b[?1006h')")   # a full-screen TUI that reports the mouse
    page.locator("#tab-bar [data-sid='s501']").first.click()
    page.wait_for_timeout(300)
    page.evaluate("document.querySelector('#tab-bar #chat-switch button[data-mode=\\\"cli\\\"]').focus()")
    box = page.locator(".xterm-screen:visible").first.bounding_box()
    page.mouse.click(box["x"] + 120, box["y"] + 60)
    page.wait_for_timeout(200)
    check("the click gives the terminal the keyboard",
          page.evaluate("document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea')"))
    page.evaluate("window._pushOutput('s501', '\\x1b[?1003l\\x1b[?1006l\\x1b[?1049l')")

    print(f"[{engine}] 2b. a visible text box keeps its keys")
    box = page.locator("#sidebar input[type=search], #sidebar input").first
    if box.count():
        box.click()
        before = len(writes())
        page.keyboard.type("xy")
        page.wait_for_timeout(200)
        check("the sidebar's search gets them, the terminal does not", writes()[before:] == [] and "xy" in box.input_value(),
              (writes()[before:], box.input_value()))
        box.fill("")
    else:
        print("  (no sidebar text box in this layout: 2b not run)")

    print(f"[{engine}] 3. the chat view is up")
    page.evaluate("window.SFChat.setMode('s501', 'chat')")
    page.wait_for_function(SHOWN)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    before = len(writes())
    page.keyboard.type("zz")
    page.wait_for_timeout(200)
    check("nothing is taken for the terminal", writes()[before:] == [], writes()[before:])
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
