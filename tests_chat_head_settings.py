#!/usr/bin/env python3
"""The chat view's own face opens the conversation's settings: web/index.html in a browser with
a stand-in Python side.

Reported in daily use: the small face at the top left of the chat view, beside the conversation's
big one, was one face too many. In the chat view the small one (bots.js's #sfb-convo, in the
terminal's gutter) goes, the view takes the gutter back, and the header's face opens the same
settings; the terminal keeps its small face, the only one it has.
  - chat view: no small face, no gutter; the header's face is a button with a hint
  - clicking it, or Enter on it, opens the settings, next to it, and they stay open
  - the terminal: in the chat style its header has the face; in the CLI style the small one is back
  - bots off: the face is only a face
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_chat_head_settings.py
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
    print("SKIP  tests_chat_head_settings.py（沒裝 playwright）")
    sys.exit(0)

ROSTER = {"助理": {"label": "助理", "cmd": "claude",
                   "bot": {"avatar": {"shape": "star", "color": "purple"}, "memory": True, "primary": True}}}
BOTS = {"enabled": True, "primary": "助理", "memory_root": "~/.config/shellframe/bots",
        "sessions": {"s501": {"bot": "助理"}}, "kinds": {"s501": "bot"}, "roster": ROSTER}
SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "助理", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "你好"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "你好，有什麼要做的？"}]}


def stub(bots_on):
    cfg = {"settings": {"language": "zh-TW", "sidebar_open": True, "ui_style": "chat", "experimental_bots": bots_on},
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
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, SESSIONS, VIEW, BOTS if bots_on else {"enabled": False}))


SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"
FACE = "#chat-head [data-sfb-settings]"
SMALL_SHOWN = "(() => { const c = document.getElementById('sfb-convo'); return !!c && !c.hidden && c.getClientRects().length > 0; })()"
POP_OPEN = "(() => { const p = document.querySelector('.sfb-pop'); return !!p && p.getClientRects().length > 0; })()"


def open_page(browser, bots_on):
    page = browser.new_page(viewport={"width": 1100, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(bots_on))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_timeout(1200)
    return page, errors


def run(browser, engine):
    print(f"[{engine}] 1. the chat view, bots on")
    page, errors = open_page(browser, True)
    check("no small face beside the conversation's own", not page.evaluate(SMALL_SHOWN))
    gap = page.evaluate("document.getElementById('chat-layer').getBoundingClientRect().left"
                        " - document.getElementById('terminal-wrap').getBoundingClientRect().left")
    check("…and no gutter left for it", abs(gap) < 0.5, gap)
    face = page.locator(FACE)
    check("the header's face is a button", face.count() == 1 and page.evaluate(
        f"document.querySelector('{FACE}').tagName") == "BUTTON")
    check("…with a hint", "這個對話的設定" in (face.get_attribute("title") or ""), face.get_attribute("title"))
    face.hover()
    check("…and a pointing hand over it",
          page.evaluate(f"getComputedStyle(document.querySelector('{FACE} .cm-face')).cursor") == "pointer")

    print(f"[{engine}] 2. clicking it")
    face.click()
    page.wait_for_timeout(300)
    check("the conversation's settings open", page.evaluate(POP_OPEN))
    near = page.evaluate(f"""() => {{ const p = document.querySelector('.sfb-pop').getBoundingClientRect();
      const f = document.querySelector('{FACE}').getBoundingClientRect();
      return p.left >= f.right && p.left - f.right < 24 && p.top <= f.bottom && p.bottom >= f.top; }}""")
    check("…right beside the face, not somewhere else", near)       # bots.js puts them right of their anchor
    page.wait_for_timeout(500)
    check("…and stay open (the outside-click rule lets the face through)", page.evaluate(POP_OPEN))
    if engine == "chromium":
        page.screenshot(path=str(HERE / "qa-shots" / "chat-head-settings.png"))
    page.mouse.click(700, 400)
    page.wait_for_timeout(200)
    check("a click elsewhere closes them", not page.evaluate(POP_OPEN))

    print(f"[{engine}] 3. the keyboard")
    face.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check("Enter on the face opens them too", page.evaluate(POP_OPEN))
    page.mouse.click(700, 400)

    print(f"[{engine}] 4. back to the terminal")
    page.evaluate("window.SFChat.setMode('s501', 'cli')")
    page.wait_for_timeout(400)
    check("chat style: the terminal's header has the face, not the gutter",
          not page.evaluate(SMALL_SHOWN) and page.locator("#term-head [data-sfb-settings]").count() == 1)
    page.locator("#term-head [data-sfb-settings]").click()
    page.wait_for_timeout(300)
    check("…and it opens the settings too", page.evaluate(POP_OPEN))
    page.mouse.click(700, 400)
    page.evaluate("window.SFChat.setStyle('cli')")
    page.wait_for_timeout(400)
    check("the CLI style: the terminal has its small face again", page.evaluate(SMALL_SHOWN))
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 5. bots off")
    page, errors = open_page(browser, False)
    check("the face is only a face: no button, no settings", page.locator(FACE).count() == 0)
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
