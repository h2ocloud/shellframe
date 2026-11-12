#!/usr/bin/env python3
"""Chat style without the tab bar: web/index.html in a browser with a stand-in Python side.

Asked for in daily use: with the chat style on, hide the row of tabs at the top and add
conversations from the sidebar. What that row held must not just vanish:
  - the sidebar lists the tabs, with 「＋ 新對話」 on top (it opens the same picker as the bar's +)
  - usage, Telegram and loops move to the sidebar's foot; ☰ in the header shows/hides the sidebar
  - a tab's terminal has a header of the view's height holding the Chat/Terminal switch: the
    terminal starts under it (nothing covers its text) and keeps its size in both modes
  - back in the CLI style everything is where it was, in the same order
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_layout.py
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
    print("SKIP  tests_chat_layout.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"助理": {"label": "助理", "cmd": "claude",
                   "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}}}
BOTS = {"enabled": True, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
        "sessions": {"s501": {"bot": "助理"}}, "kinds": {"s501": "bot", "s502": "temp"}, "roster": ROSTER}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "助理", "bridge_enabled": True},
            {"sid": "s502", "cmd": "claude", "alive": True, "label": "週報草稿", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}


def stub(style):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": style, "experimental_bots": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": ROSTER}
    return """
window.__cfg = %s; window.__sessions = %s; window.__view = %s; window.__bots = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'save_settings': window.__cfg.settings = JSON.parse(args[0]); return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's501'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'bots_state': return R(window.__bots);
      case 'preset_groups': return R([]);
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, SESSIONS, VIEW, BOTS))


SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
VISIBLE = "sel => { const el = document.querySelector(sel); return !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'; }"
BAR_IDS = "() => [...document.getElementById('tab-bar').children].map(e => e.id).filter(Boolean)"
RECT = "sel => { const b = document.querySelector(sel).getBoundingClientRect(); return [b.left, b.top, b.width, b.height, b.bottom]; }"
MOVED = ["chat-switch", "btn-board", "usage-brief", "btn-bridge"]


def open_page(browser, style):
    page = browser.new_page(viewport={"width": 1100, "height": 760})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(style))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function("!!document.getElementById('chat-switch')", timeout=15000)
    page.wait_for_timeout(1200)
    return page, errors


def run(browser, engine):
    print(f"[{engine}] 0. the CLI style: the bar as it always was")
    page, errors = open_page(browser, "cli")
    home = page.evaluate(BAR_IDS)
    check("the bar holds the switch, usage and Telegram", all(i in home for i in ("chat-switch", "usage-brief", "btn-bridge")), home)
    check("no 「＋ 新對話」 and no terminal header", not page.evaluate(VISIBLE, "#sb-new-convo")
          and not page.evaluate(VISIBLE, "#term-head"))

    print(f"[{engine}] 1. switched to the chat style")
    page.evaluate("window.SFChat.setStyle('chat')")
    page.wait_for_function(SHOWN, timeout=5000)
    page.wait_for_timeout(400)
    check("the tab bar is gone", not page.evaluate(VISIBLE, "#tab-bar"))
    check("「＋ 新對話」 tops the sidebar", page.evaluate(VISIBLE, "#sb-new-convo") and page.evaluate(
        "document.getElementById('sidebar').firstElementChild.id") == "sb-new-convo"
          and "新對話" in page.inner_text("#sb-new-convo"))
    check("usage, Telegram and loops are at the sidebar's foot", page.evaluate(
        "['usage-brief', 'btn-bridge', 'btn-board'].every(id => document.getElementById(id).closest('#sb-dock'))"))
    check("…usage shows there", page.evaluate(VISIBLE, "#sb-dock #usage-brief"))
    page.click("#sb-new-convo")
    page.wait_for_timeout(300)
    check("「＋ 新對話」 opens the same picker as the bar's +",
          page.evaluate("document.getElementById('modal-new').classList.contains('open')"))
    page.keyboard.press("Escape")
    page.evaluate("document.getElementById('modal-new').classList.remove('open')")
    page.click("#chat-head .cm-side-btn")
    page.wait_for_timeout(250)
    check("☰ in the header hides the sidebar", page.evaluate("document.getElementById('sidebar').classList.contains('collapsed')"))
    page.click("#chat-head .cm-side-btn")
    page.wait_for_timeout(250)
    check("…and brings it back", not page.evaluate("document.getElementById('sidebar').classList.contains('collapsed')"))
    pane_chat = page.evaluate(RECT, ".term-pane.active")
    chat_head_h = page.evaluate(RECT, "#chat-head")[3]
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-layout-chat.png"))

    print(f"[{engine}] 2. the tab shows its terminal")
    page.evaluate("window.SFChat.setMode('s501', 'cli')")
    page.wait_for_timeout(400)
    check("the terminal has a header", page.evaluate(VISIBLE, "#term-head"))
    check("…with the Chat/Terminal switch in it", page.evaluate("!!document.querySelector('#term-head #chat-switch')")
          and page.evaluate(VISIBLE, "#chat-switch"))
    head, pane = page.evaluate(RECT, "#term-head"), page.evaluate(RECT, ".term-pane.active")
    check(f"the terminal starts under it, nothing over its text (head ends {head[4]:.0f}, terminal starts {pane[1]:.0f})",
          abs(pane[1] - head[4]) <= 1.5, (head, pane))
    check(f"…as tall as the chat view's ({head[3]:.0f} / {chat_head_h:.0f}px)", abs(head[3] - chat_head_h) <= 1.5)
    check("the terminal keeps its size in both modes (no resize when switching)", pane == pane_chat, (pane, pane_chat))
    if engine == "webkit":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-layout-terminal.png"))
    page.click('#chat-switch button[data-mode="chat"]')
    page.wait_for_function(SHOWN, timeout=5000)
    check("its 「對話」 brings the conversation back", page.evaluate(SHOWN))

    print(f"[{engine}] 3. back to the CLI style")
    page.evaluate("window.SFChat.setStyle('cli')")
    page.wait_for_timeout(400)
    check("the tab bar is back", page.evaluate(VISIBLE, "#tab-bar"))
    check("…with everything in it, in the same order", page.evaluate(BAR_IDS) == home, (page.evaluate(BAR_IDS), home))
    check("no 「＋ 新對話」, no terminal header", not page.evaluate(VISIBLE, "#sb-new-convo")
          and not page.evaluate(VISIBLE, "#term-head"))
    page.evaluate("window.SFChat.setMode('s501', 'cli')")
    page.wait_for_timeout(300)
    check("the terminal starts at the top again", abs(page.evaluate(RECT, ".term-pane.active")[1]
                                                     - page.evaluate(RECT, "#terminal-wrap")[1]) <= 1.5)
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
