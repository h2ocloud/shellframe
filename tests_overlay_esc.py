#!/usr/bin/env python3
"""Esc on a ShellBot window over the terminal closes that window and never reaches the CLI under it:
web/index.html in a browser with a stand-in Python side.

Reported in daily use: pressing Esc to close the guide list (導覽) interrupted the Claude Code conversation in the
terminal underneath. The list had no Esc of its own, and WebKit does not move the focus to a clicked button, so the
terminal still had it: Esc went to the CLI, which reads it as "interrupt".
  - baseline: Esc in the terminal reaches the CLI (write_input "\\x1b")
  - the guide list: opening it moves the focus into it; Esc closes it, nothing reaches the CLI, the terminal gets
    the focus back
  - the same with the focus left in the terminal while the list is open (what WebKit does): the terminal ignores
    Esc while a window is over it (index.html overlayOverTerminal), the list closes
  - Settings, with the focus in the terminal: Esc closes it, nothing reaches the CLI
  - everything closed: Esc reaches the CLI again
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_overlay_esc.py
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
    print("SKIP  tests_overlay_esc.py（沒裝 playwright）")
    sys.exit(0)

CFG = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "cli"},
       "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁", "bridge_enabled": True}]
GUIDE = {"success": True, "version": "0.59.0", "state": {"seen": "0.59.0", "tour": "done"}, "tours": {"first_run": [], "entry": []},
         "features": [{"id": "new-tab", "surfaces": ["desktop"], "since": "0.44.6",
                       "title": {"en": "New tab", "zh": "開分頁"}, "body": {"en": "Open a tab.", "zh": "開一個分頁。"}}]}
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__guide = %s;
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
      case 'guide_data': return R(window.__guide);
      case 'guide_mark': return R({success: true, state: window.__guide.state});
      default: return R('{}');
    }
  };
}})};
""" % (json.dumps(CFG, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(GUIDE, ensure_ascii=False))

TERM = ".xterm-helper-textarea"
IN_TERM = f"(() => {{ const e = document.activeElement; return !!(e && e.matches && e.matches('{TERM}')); }})()"
HELP_OPEN = "!!document.querySelector('.sfg-help-veil')"
SETTINGS_OPEN = "!!document.querySelector('#modal-settings.open')"


def run(browser, engine):
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector(TERM, state="attached", timeout=15000)
    page.wait_for_function("window.SFGuide && document.getElementById('btn-guide')", timeout=15000)
    page.wait_for_timeout(2500)                       # the sidebar's opening redraws and the guide's own load are over
    escs = lambda: page.evaluate("window.__calls.filter(c => c[0] === 'write_input' && String(c[1][1]).includes('\\x1b')).length")

    def to_term():
        page.evaluate(f"document.querySelector('{TERM}').focus()")
        page.wait_for_function(IN_TERM, timeout=3000)

    def esc_count_after_escape():
        before = escs()
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        return escs() - before

    print(f"[{engine}] baseline")
    to_term()
    check("Esc in the terminal reaches the CLI (write_input \\x1b)", esc_count_after_escape() == 1)

    print(f"[{engine}] the guide list")
    to_term()
    page.evaluate("SFGuide.openHelp()")
    page.wait_for_function(HELP_OPEN, timeout=5000)
    page.wait_for_timeout(200)
    check("opening it moves the focus into it (off the terminal)",
          page.evaluate("!!(document.activeElement && document.activeElement.closest && document.activeElement.closest('.sfg-help'))"))
    n = esc_count_after_escape()
    check("Esc closes it, and nothing reaches the CLI", n == 0 and not page.evaluate(HELP_OPEN), (n, page.evaluate(HELP_OPEN)))
    check("…the terminal has the focus back", page.evaluate(IN_TERM))

    page.evaluate("SFGuide.openHelp()")
    page.wait_for_function(HELP_OPEN, timeout=5000)
    to_term()                                        # what WebKit does after a click on a button: the terminal keeps it
    n = esc_count_after_escape()
    check("with the focus left in the terminal: Esc still closes the list and nothing reaches the CLI",
          n == 0 and not page.evaluate(HELP_OPEN), (n, page.evaluate(HELP_OPEN)))

    print(f"[{engine}] Settings")
    page.click("#btn-settings")
    page.wait_for_function(SETTINGS_OPEN, timeout=5000)
    to_term()
    n = esc_count_after_escape()
    check("Esc closes Settings, and nothing reaches the CLI", n == 0 and not page.evaluate(SETTINGS_OPEN), (n, page.evaluate(SETTINGS_OPEN)))

    print(f"[{engine}] everything closed")
    to_term()
    check("Esc reaches the CLI again", esc_count_after_escape() == 1)
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
