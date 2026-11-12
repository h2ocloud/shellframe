#!/usr/bin/env python3
"""The sidebar shows no wider than the window allows: web/index.html in a browser with a stand-in
Python side. Only what is drawn is limited; the saved width is never touched.

Measured on Windows at 125% scaling: the window stops at 800px, which is 640 CSS pixels; with the
sidebar dragged to its widest (448 saved, 480 is the most) the chat view had ~150px, its title and
its input box a few letters wide. Evidence: the 4060 smoke test's "6-" screenshots.
  - 640 CSS px (an 800px window at 125%), 448 saved: the main column keeps its minimum, the sidebar
    shows narrower, and the chat view's title and input box are readable
  - the saved width is not changed (settings, --sidebar-w, no save): the window widened again, the
    sidebar is as wide as it was saved, and narrowed again, it is limited again
  - Large interface size (the sidebar is zoomed by it), and a change of the size while it shows
  - a drag cannot go past what the window allows, and saves the width it ended on; a click on the
    handle without a drag saves nothing
  - the right panel open takes its width from the room
Run in Chromium (Windows' WebView2 is one), and in WebKit (the Mac app's engine) when playwright has it
installed. Demo data only. Screenshots: qa-shots/sidebar-limit-640-chromium.png
(qa-shots/sidebar-limit-640-before-chromium.png is the same window before the change).
Needs playwright; SKIP without it. Run: .venv/bin/python tests_sidebar_limit.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE / "qa-shots"
MAIN_MIN = 400
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
    print("SKIP  tests_sidebar_limit.py（沒裝 playwright）")
    sys.exit(0)

TITLE = "demo 分頁｜窄視窗與中文標題"
SESSIONS = [{"sid": "s301", "cmd": "claude", "alive": True, "label": TITLE, "bridge_enabled": True}]
VIEW = {"success": True, "readable": True, "provider": "claude", "activity": "done", "decision": None, "rev": "r1",
        "turns": [{"kind": "user_msg", "ts": 1794190841,
                   "text": "中文字型測試：繁體中文、全形標點「引號」，這是一段比較長的句子，用來看窄視窗時會不會好好換行。"},
                  {"kind": "assistant_text", "ts": 1794190843, "text": "收到，週報在 `reports/weekly.md`，測試全部通過。"}]}
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
      case 'get_active_tab': return R({sid: 's301'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'chat_view': return args[2] && args[2] === window.__view.rev ? R({success: true, unchanged: true, rev: args[2]}) : R(window.__view);
      default: return R('{}');
    }
  };
}})};
"""
WIDTHS = """() => { const r = id => document.getElementById(id).getBoundingClientRect().width;
  return {sidebar: r('sidebar'), main: r('terminal-wrap'), total: r('app-body')}; }"""
SAVED = "window.__sfHost.cfg().settings.sidebar_width"
SHOWN = "(() => { const l = document.getElementById('chat-layer'); return l && !l.hidden; })()"


def open_page(browser, w, h, dpr=1.0, ui_scale=1.0, saved=448):
    cfg = {"settings": {"language": "en", "sidebar_open": True, "sidebar_width": saved, "uiScale": ui_scale,
                        "ui_style": "chat", "stt_mic_button": True},
           "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}], "agent_roster": {}}
    ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=dpr)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB % (json.dumps(cfg), json.dumps(SESSIONS, ensure_ascii=False), json.dumps(VIEW, ensure_ascii=False)))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_function(SHOWN, timeout=15000)
    page.wait_for_function("document.getElementById('sidebar') && !document.getElementById('sidebar').classList.contains('collapsed')",
                           timeout=15000)
    page.wait_for_timeout(1000)                    # the sidebar's opening transition and redraws are over
    return page, errors


def widths(page):
    return page.evaluate(WIDTHS)


def saves(page):
    return page.evaluate("window.__calls.filter(c => c[0] === 'save_settings').length")


def drag_to(page, x):
    b = page.locator("#sidebar-resizer").bounding_box()
    y = b["y"] + b["height"] / 2
    page.mouse.move(b["x"] + b["width"] / 2, y)
    page.mouse.down()
    page.mouse.move(x, y, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)


def run(browser, engine):
    tag = f"[{engine}]"

    print(f"{tag} 1. 640 CSS px (an 800px window at 125%), 448 saved")
    page, errors = open_page(browser, 640, 640, dpr=1.25)
    if engine == "chromium":
        SHOTS.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOTS / "sidebar-limit-640-chromium.png"))
    w = widths(page)
    check("the main column keeps its minimum", w["main"] >= MAIN_MIN - 1, w)
    check("the sidebar shows narrower than it was saved", 140 <= w["sidebar"] < 448, w)
    title = page.locator("#chat-head .cm-who b").bounding_box()
    box = page.locator("#chat-input").bounding_box()
    check("the chat view's title is readable (>= 100px)", title is not None and title["width"] >= 100, title)
    check("…and its input box can be typed in (>= 200px)", box is not None and box["width"] >= 200, box)
    check("the saved width is untouched: settings, the variable, and nothing was saved",
          page.evaluate(SAVED) == 448 and page.evaluate("document.getElementById('sidebar').style.getPropertyValue('--sidebar-w')") == "448px"
          and saves(page) == 0, (page.evaluate(SAVED), saves(page)))

    print(f"{tag} 2. the window widened and narrowed again")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(500)
    w = widths(page)
    check("widened: the sidebar is as wide as it was saved (448)", abs(w["sidebar"] - 448) <= 1, w)
    check("…and the main column has the rest", abs(w["main"] - (1280 - 448 - 5)) <= 2, w)
    page.set_viewport_size({"width": 640, "height": 640})
    page.wait_for_timeout(500)
    w = widths(page)
    check("narrowed again: limited again", w["main"] >= MAIN_MIN - 1 and w["sidebar"] < 448, w)
    check("still nothing saved, still 448", saves(page) == 0 and page.evaluate(SAVED) == 448, (saves(page), page.evaluate(SAVED)))

    print(f"{tag} 3. a click on the handle, no drag, saves nothing")
    b = page.locator("#sidebar-resizer").bounding_box()
    page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
    page.wait_for_timeout(200)
    check("no save, 448 kept", saves(page) == 0 and page.evaluate(SAVED) == 448, (saves(page), page.evaluate(SAVED)))

    print(f"{tag} 4. a drag at 640 cannot go past what the window allows")
    drag_to(page, 620)
    w = widths(page)
    check("the main column still keeps its minimum", w["main"] >= MAIN_MIN - 1, w)
    check("the width it ended on is what is saved (the one shown)", saves(page) == 1 and abs(page.evaluate(SAVED) - w["sidebar"]) <= 1,
          (saves(page), page.evaluate(SAVED), w))
    page.close()

    print(f"{tag} 5. a roomy window: a drag reaches 480, and no further down than 140")
    page, errors = open_page(browser, 1280, 800)
    drag_to(page, 1200)
    check("the widest a drag makes is 480", abs(widths(page)["sidebar"] - 480) <= 1 and page.evaluate(SAVED) == 480,
          (widths(page), page.evaluate(SAVED)))
    drag_to(page, 0)
    check("the narrowest is 140", abs(widths(page)["sidebar"] - 140) <= 1 and page.evaluate(SAVED) == 140,
          (widths(page), page.evaluate(SAVED)))
    check("no page errors", not errors, errors)
    page.close()

    print(f"{tag} 6. Large interface size (the sidebar is zoomed by it)")
    page, errors = open_page(browser, 800, 800, ui_scale=1.18, saved=480)
    w = widths(page)
    check("800px, Large, 480 saved: the main column keeps its minimum", w["main"] >= MAIN_MIN - 1, w)
    check("…the sidebar shows narrower than 480 x 1.18", w["sidebar"] < 480 * 1.18 - 1, w)
    check("…and the saved width is 480", page.evaluate(SAVED) == 480 and saves(page) == 0)
    page.close()

    print(f"{tag} 7. the interface size changed while it shows")
    page, errors = open_page(browser, 900, 800, saved=480)
    check("size M: 480 shows whole", abs(widths(page)["sidebar"] - 480) <= 1, widths(page))
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', 1.18)")
    page.wait_for_timeout(500)
    w = widths(page)
    check("size L: limited, the main column keeps its minimum", w["main"] >= MAIN_MIN - 1 and w["sidebar"] < 480 * 1.18 - 1, w)
    page.close()

    print(f"{tag} 8. the right panel open")
    page, errors = open_page(browser, 1100, 800, saved=480)
    check("closed: 480 shows whole", abs(widths(page)["sidebar"] - 480) <= 1, widths(page))
    page.evaluate("() => { const b = document.getElementById('board-panel'); b.style.setProperty('--board-w', '300px'); b.classList.remove('collapsed'); }")
    page.wait_for_timeout(600)
    w = widths(page)
    check("open: the main column still keeps its minimum", w["main"] >= MAIN_MIN - 1, w)
    check("…the sidebar gave way", w["sidebar"] < 480 - 1, w)
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
