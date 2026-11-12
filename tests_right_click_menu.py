#!/usr/bin/env python3
"""A right-click never pastes by itself: web/index.html in a browser with a stand-in Python side
and the real xterm.

Reported in daily use: select text in the terminal, right-click to copy, and the clipboard's text
shows up in the CLI's input box as a draft nobody meant to send (a message sent from the chat view
then waits behind it). Measured: the rule was "a selection is copied, no selection pastes", and a
TUI's redraw switches xterm's screen buffer, which drops the selection, so the click that was a copy
arrived as "nothing selected" and pasted. In the chat view it was worse: the selection there is the
page's, not xterm's, so every right-click on the conversation pasted into the terminal under it.
  - the reported order: select, the TUI redraws (the selection is gone), right-click: nothing is
    written to the CLI and the clipboard is not even read; a menu with Paste opens
  - Esc and a click elsewhere close it, nothing written; the keyboard is back in the terminal after Esc
  - Paste picked from the menu: the text goes in as a bracketed paste, once; a long text still waits
    for Enter (the large-paste bar)
  - a selection is still copied on a right-click, with no menu and no write; the click right after a
    copy opens nothing, a second one a moment later does
  - files copied in Finder / Explorer still paste from the menu: attached in an AI tab, typed as
    paths in a plain terminal
  - a context-menu key (no mouse) opens the menu too; the menu stays inside the window
  - the chat view: a selection in the conversation is copied (the words ⌘C would copy), nothing is
    written to the CLI under it; with nothing selected the menu's Paste goes into the view's own
    box
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Screenshots (WebKit): qa-shots/right-click-menu-webkit.png and
qa-shots/right-click-menu-chat-webkit.png. Needs playwright; SKIP without it.
Run: .venv/bin/python tests_right_click_menu.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
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
    print("SKIP  tests_right_click_menu.py（沒裝 playwright）")
    sys.exit(0)

SESSIONS = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "demo ai tab", "bridge_enabled": True},
            {"sid": "s502", "cmd": "bash", "alive": True, "label": "demo shell", "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841, "text": "Hello"},
                  {"kind": "assistant_text", "ts": 1794190843,
                   "text": "Hello, what shall we do today? This is a demo answer with a few more words in it."}]}
STUB = """
window.__calls = [];
window.__cfg = %s; window.__sessions = %s; window.__view = %s;
window.__clip = {text: 'demo clipboard text', files: []};
window.__active = 's501';
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
      case 'set_active_tab': window.__active = args[0]; return R('{}');
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return R(window.__view);
      case 'copy_text': return R('ok');
      case 'paste_text': return R(window.__clip.text);
      case 'get_clipboard_files': return R(window.__clip.files);
      case 'read_clipboard_image': return R('');
      default: return R('{}');
    }
  };
}})};
"""

TERM = "window.__sfHost.sessions()[window.__sfHost.active()].term"
MENU = ".sf-menu"
READS = ("paste_text", "get_clipboard_files", "read_clipboard_image")


def open_page(browser, lang="en", chat=False, w=1100, h=800):
    cfg = {"settings": {"language": lang, "sidebar_open": True, "ui_style": "chat" if chat else "cli"},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    page = browser.new_page(viewport={"width": w, "height": h})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % (json.dumps(cfg, ensure_ascii=False), json.dumps(SESSIONS, ensure_ascii=False),
                                 json.dumps(VIEW, ensure_ascii=False)))
    page.goto((HERE / "web" / "index.html").as_uri())
    if chat:
        page.wait_for_function("(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()",
                               timeout=15000)
    else:
        page.wait_for_function("window.__sfHost && Object.keys(window.__sfHost.sessions()).length > 1", timeout=15000)
    page.wait_for_timeout(1500)
    return page, errors


def calls(page, name):
    return page.evaluate("(n) => window.__calls.filter(c => c[0] === n).map(c => c[1])", name)


def writes(page):
    return calls(page, "write_input")


def reads(page):
    return sum(len(calls(page, n)) for n in READS)


def menu_open(page):
    return page.locator(MENU).count() > 0 and page.locator(MENU).first.is_visible()


def term_box(page):
    return page.locator(".xterm-screen >> visible=true").first.bounding_box()


def right_click(page, dx=100, dy=60):
    b = term_box(page)
    page.mouse.click(b["x"] + dx, b["y"] + dy, button="right")
    page.wait_for_timeout(250)


def select_then_redraw(page):
    """Select 'hello world' in the terminal, then have the TUI redraw the way a full-screen one does
    (it switches the screen buffer, which drops xterm's selection)."""
    page.evaluate("window._pushOutput(window.__sfHost.active(), 'hello world line one\\r\\nsecond line here\\r\\n')")
    page.wait_for_timeout(250)
    page.evaluate(f"{TERM}.select(0, 0, 11)")
    sel = page.evaluate(f"{TERM}.getSelection()")
    page.evaluate("window._pushOutput(window.__sfHost.active(), '\\x1b[?1049h\\x1b[Hredrawn\\x1b[?1049l')")
    page.wait_for_function(f"{TERM}.getSelection() === ''", timeout=5000)
    return sel


def term_focused(page):
    return page.evaluate("!!(document.activeElement && document.activeElement.classList.contains('xterm-helper-textarea'))")


def run(browser, engine):
    tag = f"[{engine}]"

    print(f"{tag} 1. the reported order: select, the TUI redraws, right-click")
    page, errors = open_page(browser)
    sel = select_then_redraw(page)
    check("the selection was there, and the redraw dropped it", sel == "hello world", sel)
    right_click(page)
    check("nothing is written to the CLI", writes(page) == [], writes(page))
    check("the clipboard is not even read (nothing was asked for)", reads(page) == 0, reads(page))
    check("the menu is open, with Paste", menu_open(page) and "Paste" in page.inner_text(MENU), page.locator(MENU).count())
    logged = [json.loads(c[1]) for c in calls(page, "js_debug") if c[0] == "rightclick"]
    check("the debug log says it was a menu, not a paste", [x["action"] for x in logged] == ["menu"], logged)
    if engine == "webkit":
        SHOTS.mkdir(exist_ok=True)
        b = term_box(page)
        page.screenshot(path=str(SHOTS / "right-click-menu-webkit.png"),
                        clip={"x": b["x"], "y": b["y"], "width": 420, "height": 200})
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check("Esc closes it", not menu_open(page))
    check("nothing was written, and Esc did not reach the CLI", writes(page) == [], writes(page))
    check("the keyboard is back in the terminal", term_focused(page))

    print(f"{tag} 2. a click elsewhere closes it")
    right_click(page)
    check("open again", menu_open(page))
    page.mouse.click(5, 300)                       # the sidebar
    page.wait_for_timeout(200)
    check("closed", not menu_open(page))
    check("still nothing written", writes(page) == [], writes(page))

    print(f"{tag} 3. Paste picked from the menu")
    right_click(page)
    page.click(f"{MENU} .sf-menu-item")
    page.wait_for_timeout(400)
    check("the menu is gone", not menu_open(page))
    check("the text went in once, as a bracketed paste",
          writes(page) == [["s501", "\x1b[200~demo clipboard text\x1b[201~"]], writes(page))
    logged = [json.loads(c[1])["action"] for c in calls(page, "js_debug") if c[0] == "rightclick"]
    check("the log has the menu, then the pick", logged == ["menu", "menu", "menu", "menu-paste"][:len(logged)]
          and logged[-1] == "menu-paste", logged)
    check("the keyboard is in the terminal", term_focused(page))
    page.close()

    print(f"{tag} 4. Enter on the menu's item pastes too (the keyboard has it)")
    page, errors = open_page(browser)
    right_click(page)
    check("the item has the keyboard", page.evaluate("document.activeElement && document.activeElement.classList.contains('sf-menu-item')"))
    page.keyboard.press("Enter")
    page.wait_for_timeout(400)
    check("Enter picked Paste", writes(page) == [["s501", "\x1b[200~demo clipboard text\x1b[201~"]], writes(page))
    page.close()

    print(f"{tag} 5. a long text still waits for Enter")
    page, errors = open_page(browser)
    page.evaluate("window.__clip.text = 'a long line of demo text. '.repeat(60)")
    right_click(page)
    page.click(f"{MENU} .sf-menu-item")
    page.wait_for_timeout(400)
    check("the large-paste bar is up and nothing is written yet",
          page.evaluate("getComputedStyle(document.getElementById('paste-confirm-bar')).display") != "none" and writes(page) == [],
          writes(page))
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    check("Enter sends it as a bracketed paste",
          len(writes(page)) == 1 and writes(page)[0][1].startswith("\x1b[200~a long line") and writes(page)[0][1].endswith("\x1b[201~"))
    page.close()

    print(f"{tag} 6. a selection is still copied: no menu, no write")
    page, errors = open_page(browser)
    page.evaluate("window._pushOutput('s501', 'hello world line one\\r\\nsecond line here\\r\\n')")
    page.wait_for_timeout(250)
    page.evaluate(f"{TERM}.select(0, 0, 11)")
    right_click(page)
    check("copy_text got the selection, tagged as the live right-click",
          calls(page, "copy_text") == [["hello world", "live-rightclick"]], calls(page, "copy_text"))
    check("no menu", not menu_open(page))
    check("nothing written, nothing read", writes(page) == [] and reads(page) == 0, (writes(page), reads(page)))
    check("the highlight is dropped once the copy is confirmed", page.evaluate(f"{TERM}.getSelection()") == "")

    print(f"{tag} 7. the click right after a copy opens nothing; one a moment later does")
    right_click(page)
    check("right after the copy: no menu (that click's twin)", not menu_open(page))
    page.wait_for_timeout(1600)
    right_click(page)
    check("a moment later: the menu", menu_open(page))
    check("still nothing written", writes(page) == [], writes(page))
    page.close()

    print(f"{tag} 8. files copied in Finder / Explorer still paste from the menu")
    page, errors = open_page(browser)
    page.evaluate("window.__clip.files = ['/demo/a.txt', '/demo/b.txt']")
    right_click(page)
    page.click(f"{MENU} .sf-menu-item")
    page.wait_for_timeout(500)
    check("an AI tab attaches them: a bracketed path each, and two chips",
          writes(page) == [["s501", " \x1b[200~/demo/a.txt\x1b[201~"], ["s501", " \x1b[200~/demo/b.txt\x1b[201~"]]
          and page.locator("#attach-chips .attach-chip").count() == 2, writes(page))
    check("the clipboard's text was not pasted on top", all("demo clipboard text" not in w[1] for w in writes(page)))
    page.evaluate("window.__sfHost.switchTab('s502')")
    page.wait_for_timeout(500)
    before = len(writes(page))
    right_click(page)
    page.click(f"{MENU} .sf-menu-item")
    page.wait_for_timeout(500)
    check("a plain terminal gets the paths typed",
          writes(page)[before:] == [["s502", "/demo/a.txt /demo/b.txt"]], writes(page)[before:])
    page.close()

    print(f"{tag} 9. the context-menu key (no mouse), and the edges of the window")
    page, errors = open_page(browser)
    page.dispatch_event(".xterm-screen >> visible=true", "contextmenu")
    page.wait_for_timeout(250)
    check("the menu key opens the menu, and nothing is written", menu_open(page) and writes(page) == [] and reads(page) == 0,
          (menu_open(page), writes(page)))
    page.keyboard.press("Escape")
    right_click(page, 700, 700)                    # the bottom right of the terminal area
    page.wait_for_timeout(200)
    b = page.locator(MENU).bounding_box()
    check("the menu stays inside the window", b is not None and b["x"] >= 0 and b["y"] >= 0
          and b["x"] + b["width"] <= 1100 and b["y"] + b["height"] <= 800, b)
    page.keyboard.press("Escape")
    page.mouse.wheel(0, 120)
    right_click(page)
    page.mouse.wheel(0, 120)
    page.wait_for_timeout(200)
    check("a scroll closes it", not menu_open(page))
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 10. in Traditional Chinese")
    page, errors = open_page(browser, lang="zh-TW")
    right_click(page)
    check("the item says 貼上", menu_open(page) and "貼上" in page.inner_text(MENU), page.locator(MENU).count())
    page.close()

    print(f"{tag} 11. the chat view: the selection is the page's, and a paste goes into its own box")
    page, errors = open_page(browser, chat=True)
    page.evaluate("window._pushOutput('s501', 'terminal text of the old screen\\r\\n')")
    page.wait_for_timeout(250)
    page.evaluate(f"{TERM}.select(0, 0, 8)")        # a selection left in the hidden terminal must not be what a copy takes
    msg = page.locator("#chat-list").get_by_text("Hello, what shall we do today?").first
    msg.click(click_count=3)
    page.wait_for_timeout(150)
    msg.click(button="right")
    page.wait_for_timeout(300)
    got = calls(page, "copy_text")
    check("the conversation's selection was copied, tagged for the chat view",
          len(got) == 1 and got[0][0].startswith("Hello, what shall we do today?") and got[0][1] == "chat-rightclick", got)
    check("…not the terminal's old selection", all("terminal" not in g[0] for g in got), got)
    check("no menu, and nothing written to the CLI under the view", not menu_open(page) and writes(page) == [] and reads(page) == 0,
          (menu_open(page), writes(page)))
    page.wait_for_timeout(1600)
    page.evaluate("window.getSelection().removeAllRanges(); window.__sfHost.sessions()[window.__sfHost.active()].term.clearSelection()")
    msg.click(button="right")
    page.wait_for_timeout(300)
    check("nothing selected: the menu", menu_open(page) and writes(page) == [], writes(page))
    if engine == "webkit":
        page.screenshot(path=str(SHOTS / "right-click-menu-chat-webkit.png"),
                        clip={"x": 240, "y": 100, "width": 700, "height": 360})
    page.click(f"{MENU} .sf-menu-item")
    page.wait_for_timeout(400)
    check("Paste went into the chat's input box", page.input_value("#chat-input") == "demo clipboard text", page.input_value("#chat-input"))
    check("…and the CLI under the view got nothing", writes(page) == [], writes(page))
    check("the keyboard is in the box", page.evaluate("document.activeElement && document.activeElement.id") == "chat-input")
    page.keyboard.type(" more")
    check("typing goes on in the box", page.input_value("#chat-input") == "demo clipboard text more" and writes(page) == [],
          page.input_value("#chat-input"))
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
