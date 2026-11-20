#!/usr/bin/env python3
"""The Chat/Terminal switch changes every tab at once (settings.ui_style); an Advanced option makes it one tab only.

web/index.html in a browser with a stand-in Python side. Asked for by the person using it: switching one tab and finding the
others still on the other view was one step too many, so the switch (the button at the top, Cmd/Ctrl+Shift+M, the view's own
Terminal button) is now the Interface style setting, saved. Settings -> Advanced -> `view_switch_per_tab` (off by default) brings
back "only the tab you are on".
  - default: the Chat button shows the conversation on every tab that has one, saves ui_style, and a tab with no conversation (a
    shell) stays a terminal; the shortcut flips them all back; a tab opened later follows the setting; the tooltips say "all tabs"
  - per tab on: the button and the shortcut change only the active tab and ui_style stays as it was; the tooltips say "this tab"
  - Settings -> Advanced has the switch (off by default); it writes view_switch_per_tab through save_settings, and turning it either
    way puts every tab back on ui_style
  - English words
Demo data only. Needs playwright; SKIP without it. Screenshot: qa-shots/chat-view-switch-setting.png.
Run: .venv/bin/python tests_chat_view_switch.py
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
        print(f"  [FAIL] {name}  {str(detail)[:400]}")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_chat_view_switch.py（沒裝 playwright）")
    sys.exit(0)

SESSIONS = [{"sid": "s601", "cmd": "claude", "alive": True, "label": "示範分頁甲", "bridge_enabled": True},
            {"sid": "s602", "cmd": "claude", "alive": True, "label": "示範分頁乙", "bridge_enabled": True},
            {"sid": "s603", "cmd": "zsh", "alive": True, "label": "終端機", "bridge_enabled": True}]
CHAT = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"}, {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}
SHELL = {"success": True, "readable": False, "why": "provider", "provider": "other", "activity": "", "decision": None, "rev": "r2", "turns": []}
VIEWS = {"s601": CHAT, "s602": dict(CHAT, rev="r3"), "s603": SHELL}
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
"""

CHAT_BTN = '#chat-switch button[data-mode="chat"]'
TERM_BTN = '#chat-switch button[data-mode="cli"]'
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return !!l && !l.hidden; })()"


def cfg(lang="zh-TW", per_tab=None, style="cli"):
    st = {"language": lang, "sidebar_open": True, "ui_style": style}
    if per_tab is not None:
        st["view_switch_per_tab"] = per_tab
    return {"settings": st, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}


def open_page(browser, **kw):
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.add_init_script(STUB % (json.dumps(cfg(**kw), ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEWS, ensure_ascii=False)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!document.querySelector('%s')" % CHAT_BTN, timeout=15000)
    page.wait_for_function("document.querySelector('%s') && !document.querySelector('%s').disabled" % (CHAT_BTN, CHAT_BTN), timeout=8000)
    return page


def go(page, sid):
    page.evaluate("(sid) => { window.__active = sid; window.__sfHost.switchTab(sid); }", sid)
    page.wait_for_timeout(500)


def shown(page):
    return page.evaluate(SHOWN)


def style(page):
    return page.evaluate("window.__cfg.settings.ui_style")


def shortcut(page):
    page.evaluate("""() => { const mac = /MAC/i.test(navigator.platform || navigator.userAgent || '');
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'm', shiftKey: true, metaKey: mac, ctrlKey: !mac, bubbles: true, cancelable: true})); }""")
    page.wait_for_timeout(500)


def title(page, sel):
    return page.evaluate("(s) => document.querySelector(s).title", sel)


with sync_playwright() as pw:
    browser = pw.chromium.launch()

    print("1. default: the switch changes every tab")
    page = open_page(browser)
    check("starts on the terminal everywhere", not shown(page) and style(page) == "cli")
    check("the tooltips say all tabs (zh)", title(page, CHAT_BTN).startswith("全部分頁看對話") and title(page, TERM_BTN).startswith("全部分頁看終端機"), (title(page, CHAT_BTN), title(page, TERM_BTN)))
    page.locator(CHAT_BTN).click()
    page.wait_for_timeout(600)
    check("the Chat button shows the conversation", shown(page))
    check("…and it is the setting: ui_style saved as chat through save_settings", style(page) == "chat" and page.evaluate("window.__calls.some(c => c[0] === 'save_settings')"), style(page))
    go(page, "s602")
    check("another tab with a conversation is on chat as well (not just the one pressed)", shown(page) and page.evaluate("SFChat.modeOf('s602')") == "chat")
    go(page, "s603")
    check("a tab with no conversation (a shell) stays on its terminal", not shown(page) and page.evaluate("SFChat.modeOf('s603')") == "chat")
    check("…its Chat button is greyed", page.evaluate("document.querySelector('%s').disabled" % CHAT_BTN))
    go(page, "s601")
    check("back on the first tab: still chat", shown(page))
    shortcut(page)
    check("the shortcut sends every tab to the terminal and saves ui_style cli", not shown(page) and style(page) == "cli"
          and page.evaluate("SFChat.modeOf('s602')") == "cli", style(page))
    go(page, "s602")
    check("…the other tab is on its terminal too", not shown(page))
    shortcut(page)
    check("the shortcut again: all on chat", shown(page) and style(page) == "chat")
    go(page, "s601")
    page.evaluate("document.querySelector('#chat-head .cm-to-term').click()")
    page.wait_for_timeout(500)
    check("the view's own Terminal button does the same: everyone, saved", not shown(page) and style(page) == "cli")
    page.evaluate("window.__sessions.push({sid: 's604', cmd: 'claude', alive: true, label: '新分頁', bridge_enabled: true}); window.__views.s604 = %s;" % json.dumps(CHAT))
    check("a tab opened later follows the setting (no private choice kept)", page.evaluate("SFChat.modeOf('s604')") == "cli")
    shortcut(page)
    check("…and chat once the setting is chat", page.evaluate("SFChat.modeOf('s604')") == "chat")
    go(page, "s603")
    shortcut(page)
    check("on a shell tab the shortcut still sends everyone back to the terminals", style(page) == "cli")
    shortcut(page)
    check("…but 'to chat' from a tab with no conversation does nothing (nothing to show there)", style(page) == "cli")
    check("no page errors", not page.errors, page.errors)
    page.close()

    print("2. per tab (Advanced option on): only the active tab")
    page = open_page(browser, per_tab=True)
    check("the tooltips say this tab", title(page, CHAT_BTN).startswith("這個分頁看對話") and title(page, TERM_BTN).startswith("這個分頁看終端機"), title(page, CHAT_BTN))
    page.locator(CHAT_BTN).click()
    page.wait_for_timeout(500)
    check("the button shows the conversation on this tab", shown(page))
    check("…and ui_style is not touched", style(page) == "cli" and not page.evaluate("window.__calls.some(c => c[0] === 'save_settings')"), style(page))
    go(page, "s602")
    check("the other tab is still on its terminal", not shown(page) and page.evaluate("SFChat.modeOf('s602')") == "cli")
    shortcut(page)
    check("the shortcut changes only this tab", shown(page) and page.evaluate("SFChat.modeOf('s601')") == "chat" and style(page) == "cli")
    go(page, "s601")
    check("the first tab kept its own choice", shown(page))
    page.evaluate("document.querySelector('#chat-head .cm-to-term').click()")
    page.wait_for_timeout(400)
    check("its Terminal button: this tab only", not shown(page) and page.evaluate("SFChat.modeOf('s602')") == "chat")
    go(page, "s603")
    shortcut(page)
    check("a shell tab: the shortcut does nothing", not shown(page) and page.evaluate("SFChat.modeOf('s603')") == "cli")
    check("no page errors", not page.errors, page.errors)
    page.close()

    print("3. Settings -> Advanced")
    page = open_page(browser)
    page.evaluate("document.getElementById('modal-settings').classList.add('open')")
    page.evaluate("document.querySelector('.settings-tab[data-tab=\"advanced\"]').click()")
    page.wait_for_timeout(300)
    row = "#settings-panel-advanced #setting-view-per-tab"
    check("the row is there, in Chinese, and off", page.evaluate("!!document.querySelector('%s')" % row)
          and "每個分頁各自切換對話／終端機" in page.evaluate("document.querySelector('%s').closest('.sx-row').innerText" % row)
          and not page.evaluate("document.querySelector('%s').classList.contains('on')" % row)
          and page.evaluate("document.querySelector('%s').getAttribute('aria-checked')" % row) == "false")
    check("it is off by default in the saved settings too", page.evaluate("window.__cfg.settings.view_switch_per_tab") in (None, False))
    page.screenshot(path=str(HERE / "qa-shots" / "chat-view-switch-setting.png"))
    page.locator(row).click()
    page.wait_for_timeout(300)
    saved = page.evaluate("window.__calls.filter(c => c[0] === 'save_settings').map(c => JSON.parse(c[1][0]))")
    check("turning it on writes view_switch_per_tab true through save_settings, ui_style as it was",
          saved and saved[-1].get("view_switch_per_tab") is True and saved[-1].get("ui_style") == "cli", saved[-1:] if saved else saved)
    check("…the switch shows on", page.evaluate("document.querySelector('%s').classList.contains('on')" % row))
    page.evaluate("document.getElementById('modal-settings').classList.remove('open')")
    page.locator(CHAT_BTN).click()
    page.wait_for_timeout(400)
    check("…and now the button changes this tab only", shown(page) and style(page) == "cli")
    page.evaluate("document.getElementById('modal-settings').classList.add('open')")
    page.locator(row).click()
    page.wait_for_timeout(300)
    page.evaluate("document.getElementById('modal-settings').classList.remove('open')")
    check("turning it off puts every tab back on ui_style (this tab's private chat is dropped)", not shown(page)
          and page.evaluate("window.__cfg.settings.view_switch_per_tab") is False and page.evaluate("SFChat.modeOf('s601')") == "cli")
    check("the Interface style hint now says all tabs",
          "全部分頁" in page.evaluate("document.querySelector('.cm-setting .cm-set-hint').textContent"), page.evaluate("document.querySelector('.cm-setting .cm-set-hint').textContent"))
    check("no page errors", not page.errors, page.errors)
    page.close()

    print("4. English")
    page = open_page(browser, lang="en")
    page.evaluate("document.getElementById('modal-settings').classList.add('open')")
    page.evaluate("document.querySelector('.settings-tab[data-tab=\"advanced\"]').click()")
    page.wait_for_timeout(300)
    txt = page.evaluate("document.querySelector('%s').closest('.sx-row').innerText" % row)
    check("the row is in English", "Each tab switches between chat and terminal on its own" in txt and not any("一" <= ch <= "鿿" for ch in txt), txt)
    page.evaluate("document.getElementById('modal-settings').classList.remove('open')")
    check("the tooltips say all tabs (en)", title(page, CHAT_BTN).startswith("Show all tabs as conversations") and title(page, TERM_BTN).startswith("Show all tabs as terminals"),
          (title(page, CHAT_BTN), title(page, TERM_BTN)))
    page.close()
    browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
