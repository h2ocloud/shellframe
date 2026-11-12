#!/usr/bin/env python3
"""After a rename, the chat box's hint has the new name at once: web/index.html in a browser with a
stand-in Python side.

Reported (#52): renaming a tab (or a bot) changed the sidebar and the view's title, but the input
box still said "Message <old name> ..." until the person went to another tab and came back. The
hint was written only when the view was drawn for a tab; the title is redrawn every second.
  - the rename box, on a tab in the chat view: the hint names the new name right away, with the title
  - a rename the app pushes (sfctl, Telegram, a bot's rename): the same
  - a name that changed in the tab list and nothing else drew: the next tick has it
  - Traditional Chinese: the hint says it in Chinese
  - what the person is typing is not touched by the change (the text, the focus)
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_hint_rename.py
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
    print("SKIP  tests_chat_hint_rename.py（沒裝 playwright）")
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
      case 'chat_view': return args[2] && args[2] === window.__view.rev ? R({success: true, unchanged: true, rev: args[2]}) : R(window.__view);   // as the app: nothing new, nothing sent
      case 'rename_session': { const s = window.__sessions.find(x => x.sid === args[0]); if (s) s.label = args[1]; return R({success: true}); }
      default: return R('{}');
    }
  };
}})};
"""
HINT = "document.getElementById('chat-input').placeholder"
TITLE = "(document.querySelector('#chat-head .cm-who b') || {}).textContent"


def open_page(browser, lang="en"):
    cfg = {"settings": {"language": lang, "sidebar_open": True, "ui_style": "chat"},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % (json.dumps(cfg), json.dumps(SESSIONS), json.dumps(VIEW)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()",
                           timeout=15000)
    page.wait_for_timeout(1500)
    return page, errors


def run(browser, engine):
    tag = f"[{engine}]"

    print(f"{tag} 1. the rename box")
    page, errors = open_page(browser)
    check("the hint names the tab", "demo tab" in page.evaluate(HINT), page.evaluate(HINT))
    page.evaluate("window.__sfHost.renameSession('s501')")
    page.wait_for_selector("#rename-input")
    page.fill("#rename-input", "Renamed tab")
    page.click("#rename-ok")
    page.wait_for_timeout(120)                      # well inside the view's one-second tick
    check("the title has the new name", page.evaluate(TITLE) == "Renamed tab", page.evaluate(TITLE))
    check("the hint has it too, at once", "Renamed tab" in page.evaluate(HINT) and "demo tab" not in page.evaluate(HINT),
          page.evaluate(HINT))
    page.wait_for_timeout(1600)
    check("and it stays after the tick", "Renamed tab" in page.evaluate(HINT), page.evaluate(HINT))

    print(f"{tag} 2. a rename the app pushes (sfctl, Telegram, a bot's rename)")
    page.evaluate("window.__sfApplyLabel('s501', 'Pushed name')")
    page.wait_for_timeout(120)
    check("the hint follows at once", "Pushed name" in page.evaluate(HINT) and page.evaluate(TITLE) == "Pushed name",
          (page.evaluate(HINT), page.evaluate(TITLE)))

    print(f"{tag} 3. a name that came with the app's tab list, and one that changed with nothing drawing")
    page.evaluate("window.__sessions[0].label = 'Synced name'; window._syncSessionsFromBackend()")
    page.wait_for_function(f"{HINT}.includes('Synced name')", timeout=3000)
    check("the tab list's new name is in the hint", "Synced name" in page.evaluate(HINT), page.evaluate(HINT))
    page.evaluate("window.__sessions[0].label = 'Quiet change'; window.__sfHost.sessions()['s501'].label = 'Quiet change'")
    page.wait_for_function(f"{HINT}.includes('Quiet change')", timeout=3000)
    check("a name nothing redrew for: the next tick has it", "Quiet change" in page.evaluate(HINT), page.evaluate(HINT))

    print(f"{tag} 4. what is being typed is not touched")
    page.click("#chat-input")
    page.keyboard.type("half a sentence")
    page.evaluate("window.__sessions[0].label = 'Another name'; window.__sfApplyLabel('s501', 'Another name')")
    page.wait_for_timeout(200)
    check("the hint has the name", "Another name" in page.evaluate(HINT), page.evaluate(HINT))
    check("the words are still there and the box has the keyboard",
          page.input_value("#chat-input") == "half a sentence" and page.evaluate("document.activeElement.id") == "chat-input",
          page.input_value("#chat-input"))
    page.keyboard.type(" and on")
    check("typing goes on", page.input_value("#chat-input") == "half a sentence and on", page.input_value("#chat-input"))
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 5. in Traditional Chinese")
    page, errors = open_page(browser, lang="zh-TW")
    page.evaluate("window.__sfApplyLabel('s501', '新的名字')")
    page.wait_for_timeout(150)
    check("the hint says it in Chinese, with the new name", page.evaluate(HINT) == "傳訊息給 新的名字（Enter 送出，Shift+Enter 換行）",
          page.evaluate(HINT))
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
