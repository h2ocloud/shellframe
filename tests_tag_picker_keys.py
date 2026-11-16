#!/usr/bin/env python3
"""The terminal's `#` picker moves with ↑↓ while a full-screen CLI runs (web/index.html tagPickerOnData).

Reported: in the terminal, typing `#` to pick a bot or a conversation, the arrow keys went to the
terminal instead. Claude Code turns on application cursor keys (DECCKM, ESC[?1h), and xterm then sends
ESC O A / ESC O B for ↑↓; the picker knew only ESC [ A / ESC [ B, so an arrow closed it and moved the
CLI's own input history.
  - application cursor keys on: ↑↓ move the selection, nothing reaches the CLI, Enter puts in the tab's name
  - normal cursor keys: the same
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_tag_picker_keys.py
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
    print("SKIP  tests_tag_picker_keys.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True},
            {"sid": "s502", "cmd": "claude", "alive": True, "label": "示範二", "bridge_enabled": True},
            {"sid": "s503", "cmd": "claude", "alive": True, "label": "示範三", "bridge_enabled": True}]
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    window.__calls.push([prop, args]);
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False))

SEL = "(() => { const e = document.querySelector('.tag-picker.open .tp-item.sel .tp-label'); return e ? e.textContent : null; })()"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!document.querySelector('#terminal-wrap .xterm-helper-textarea')", timeout=15000)
    page.wait_for_timeout(1200)
    writes = lambda: "".join(c[1][1] for c in page.evaluate("window.__calls") if c[0] == "write_input")
    for mode, seq in (("application cursor keys (a full-screen CLI)", "\\x1b[?1h"), ("normal cursor keys", "\\x1b[?1l")):
        print(f"[{engine}] {mode}")
        page.evaluate(f"window._pushOutput('s501', '{seq}')")
        page.wait_for_timeout(150)
        page.click("#terminal-wrap .xterm")
        before = len(writes())
        page.keyboard.type("#")
        page.wait_for_selector(".tag-picker.open", timeout=3000)
        first = page.evaluate(SEL)
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(150)
        second = page.evaluate(SEL)
        check("↓ moves the selection, the picker stays open", first == "示範二" and second == "示範三", (first, second))
        page.keyboard.press("ArrowUp")
        page.wait_for_timeout(150)
        check("↑ moves it back", page.evaluate(SEL) == "示範二", page.evaluate(SEL))
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)
        sent = writes()[before:]
        check("no arrow reached the CLI", "\x1bO" not in sent and "\x1b[A" not in sent and "\x1b[B" not in sent, repr(sent))
        check("Enter puts in the picked tab's name", sent.endswith("示範二 "), repr(sent))
        check("the picker closed", not page.query_selector(".tag-picker.open"))
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
