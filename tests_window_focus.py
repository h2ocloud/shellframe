#!/usr/bin/env python3
"""The window regaining focus does not take the keyboard from an open dialog: web/index.html in a
browser with a stand-in Python side and the real xterm.

Reported on Windows: with the rename box (or Settings, the bot editor) open, switching to another
window and back, what was typed went to the CLI. The page gives the keyboard back to the terminal
150ms after the window gets focus (Cmd+Tab back); it did that whatever had the keyboard.
  - a rename box open, the window blurs and focuses: the box keeps the keyboard and what is typed
    goes into it, nothing to the CLI
  - the same with Settings open (a text box in it), and with a dialog that has no text box (the
    close-tab question: its button keeps the keyboard, Esc closes it and does not reach the CLI)
  - the chat view's own box keeps it too
  - what was always so still is: nothing has focus, or a button has it: the terminal gets it, and
    the chat view's box takes it when the view shows
  - a rename that opens a question of its own (a bot's "rename it?") keeps the keyboard there: the
    terminal does not take it back a tick later, so Enter confirms the question and does not reach the CLI
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_window_focus.py
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
    print("SKIP  tests_window_focus.py（沒裝 playwright）")
    sys.exit(0)

SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "demo tab", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "Hello"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "Hello, what shall we do today?"}]}
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
"""
BLUR_FOCUS = "window.dispatchEvent(new Event('blur')); window.dispatchEvent(new Event('focus'))"
ACTIVE = "(() => { const e = document.activeElement; return e ? (e.id || e.className || e.tagName) : ''; })()"


def open_page(browser, chat=False):
    cfg = {"settings": {"language": "en", "sidebar_open": True, "ui_style": "chat" if chat else "cli"},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % (json.dumps(cfg), json.dumps(SESSIONS), json.dumps(VIEW)))
    page.goto((HERE / "web" / "index.html").as_uri())
    if chat:
        page.wait_for_function("(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()",
                               timeout=15000)
    else:
        page.wait_for_function("window.__sfHost && Object.keys(window.__sfHost.sessions()).length > 0", timeout=15000)
    page.wait_for_timeout(1500)
    return page, errors


def writes(page):
    return page.evaluate("window.__calls.filter(c => c[0] === 'write_input').map(c => c[1])")


def window_back(page):
    """The window loses focus and gets it back; the page waits 150ms before it acts."""
    page.evaluate(BLUR_FOCUS)
    page.wait_for_timeout(450)


def term_focused(page):
    return page.evaluate("!!(document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea'))")


def run(browser, engine):
    tag = f"[{engine}]"

    print(f"{tag} 1. the rename box")
    page, errors = open_page(browser)
    page.evaluate("window.__sfHost.renameSession('s501')")
    page.wait_for_selector("#rename-input")
    check("the box has the keyboard", page.evaluate(ACTIVE) == "rename-input", page.evaluate(ACTIVE))
    window_back(page)
    check("after the window is back, it still has it", page.evaluate(ACTIVE) == "rename-input", page.evaluate(ACTIVE))
    page.keyboard.type("xy")
    page.wait_for_timeout(200)
    check("what is typed goes into the box", page.input_value("#rename-input") == "xy", page.input_value("#rename-input"))
    check("…and nothing reaches the CLI", writes(page) == [], writes(page))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check("Esc closes the box, the CLI got no Esc", page.locator("#rename-input").count() == 0 and writes(page) == [], writes(page))
    check("the terminal has the keyboard again", term_focused(page))

    print(f"{tag} 2. Settings, and a dialog with no text box")
    page.click("#btn-settings")
    page.wait_for_selector("#modal-settings.open")
    page.click("#setting-ui-prompt")
    check("a text box in Settings has the keyboard", page.evaluate(ACTIVE) == "setting-ui-prompt", page.evaluate(ACTIVE))
    window_back(page)
    check("after the window is back, it still has it", page.evaluate(ACTIVE) == "setting-ui-prompt", page.evaluate(ACTIVE))
    page.keyboard.type("note")
    page.wait_for_timeout(150)
    check("what is typed stays in Settings", writes(page) == [], writes(page))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.click('#sidebar-sessions [data-action="close"]')
    page.wait_for_selector("#close-tab-confirm")
    check("the close question's button has the keyboard", page.evaluate(ACTIVE) == "close-tab-confirm", page.evaluate(ACTIVE))
    window_back(page)
    check("after the window is back, the button still has it", page.evaluate(ACTIVE) == "close-tab-confirm", page.evaluate(ACTIVE))
    before = len(writes(page))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    check("Esc closes the question and does not reach the CLI", page.locator("#close-tab-confirm").count() == 0
          and len(writes(page)) == before, writes(page))
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 3. the old behaviour: nothing, or a button, has the keyboard")
    page, errors = open_page(browser)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    check("nothing has the keyboard", page.evaluate(ACTIVE) == "BODY", page.evaluate(ACTIVE))
    window_back(page)
    check("the terminal gets it", term_focused(page), page.evaluate(ACTIVE))
    before = len(writes(page))
    page.keyboard.type("ok")
    page.wait_for_timeout(200)
    check("and what is typed reaches the CLI", len(writes(page)) - before == 2, writes(page))
    page.evaluate("document.querySelector('#tab-bar button').focus()")
    check("a button has the keyboard", page.evaluate("document.activeElement.tagName") == "BUTTON")
    window_back(page)
    check("the terminal gets it back from the button", term_focused(page), page.evaluate(ACTIVE))
    page.close()

    print(f"{tag} 4. the rename that opens a question of its own")
    page, errors = open_page(browser)
    page.evaluate("""() => {
      window.SFBots.renameBotTab = () => {       // what bots.js does for a bot's tab: a question, taking the keyboard
        const o = document.createElement('div'); o.className = 'modal-overlay open';
        o.innerHTML = '<button id="fake-ok">OK</button>'; document.body.appendChild(o);
        o.querySelector('button').focus(); return true;
      };
      window.__sfHost.renameSession('s501');
    }""")
    page.wait_for_selector("#rename-input")
    page.fill("#rename-input", "new name")
    page.click("#rename-ok")
    page.wait_for_timeout(300)
    check("the question has the keyboard a moment after the box closed", page.evaluate(ACTIVE) == "fake-ok", page.evaluate(ACTIVE))
    page.keyboard.press("Enter")
    page.wait_for_timeout(200)
    check("Enter did not reach the CLI", writes(page) == [], writes(page))
    page.close()

    print(f"{tag} 5. the chat view")
    page, errors = open_page(browser, chat=True)
    page.click("#chat-input")
    check("the chat box has the keyboard", page.evaluate(ACTIVE) == "chat-input", page.evaluate(ACTIVE))
    window_back(page)
    check("after the window is back, it still has it", page.evaluate(ACTIVE) == "chat-input", page.evaluate(ACTIVE))
    page.keyboard.type("hello")
    check("what is typed goes into the box, not the CLI", page.input_value("#chat-input") == "hello" and writes(page) == [],
          (page.input_value("#chat-input"), writes(page)))
    page.evaluate("document.activeElement.blur()")
    window_back(page)
    check("nothing had the keyboard: the chat box takes it (as it did)", page.evaluate(ACTIVE) == "chat-input", page.evaluate(ACTIVE))
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
