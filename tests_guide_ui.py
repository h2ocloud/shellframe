#!/usr/bin/env python3
"""The guide on the page (web/guide.js): web/index.html in a browser with a stand-in Python side.

Asked for: a newly installed ShellBot walks its person through the features, and every feature
has its help, kept with the feature (guide.json; tests_guide.py binds the two).
  - a first launch (guide state "pending", no tab yet): the launcher steps aside, the folded
    sidebar opens, and a spotlight walks the first-run tour; a step in Settings opens that tab;
    "Done" records the tour done and opens the launcher again
  - a computer that already ran ShellBot ("skip"): nothing pops up; the Guide button shows how
    many features are new since the version last looked at; it opens the help list, which
    records that version as seen
  - "show me" on a feature behind a switch that is off opens Settings and points at the switch
  - English and Chinese follow the page's language; the card stays inside a small window
Run in Chromium, and in WebKit (the Mac app's engine) when playwright has it installed.
Demo data only. Needs playwright; SKIP without it. Run: .venv/bin/python tests_guide_ui.py
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import guide  # noqa: E402

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
    print("SKIP  tests_guide_ui.py（沒裝 playwright）")
    sys.exit(0)

DATA = guide.load()
VERSION = guide.app_version()


def stub(lang, tour, seen, sessions, settings_extra=None):
    settings = {"language": lang, "ui_style": "chat", "experimental_bots": True, "experimental_a2a": False}
    settings.update(settings_extra or {})
    cfg = {"settings": settings, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}],
           "agent_roster": {}, "guide": {"tour": tour, "seen": seen}}
    gdata = {"success": True, "features": guide.for_surface("desktop", settings, DATA),
             "tours": DATA.get("tours"), "state": {"tour": tour, "seen": seen, "version": VERSION}}
    return """
window.__calls = []; window.__cfg = %s; window.__sessions = %s; window.__guide = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: window.__sessions[0] ? window.__sessions[0].sid : ''});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'guide_data': return R(window.__guide);
      case 'preset_groups': return R([]);
      case 'guide_mark':
        window.__calls.push(['guide_mark', args[0], args[1]]);
        if (args[0]) window.__guide.state.tour = args[0];
        if (args[1]) window.__guide.state.seen = window.__guide.state.version;
        return R({success: true, changed: true, state: window.__guide.state});
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, sessions, gdata))


DEMO_TAB = [{"sid": "s501", "cmd": "claude", "alive": True, "label": "示範分頁"}]
CARD_TITLE = "document.querySelector('.sfg-card .sfg-title') && document.querySelector('.sfg-card .sfg-title').textContent"


def title(page):
    return page.evaluate(f"({CARD_TITLE}) || ''")


def inside(page):
    return page.evaluate("""(() => { const c = document.querySelector('.sfg-card'); if (!c) return false;
      const r = c.getBoundingClientRect(); return r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight; })()""")


def spot_on(page, sel):
    return page.evaluate("""(sel) => { const s = document.querySelector('.sfg-spot'), el = document.querySelector(sel);
      if (!s || !el) return false; const a = s.getBoundingClientRect(), b = el.getBoundingClientRect();
      return a.left <= b.left && a.top <= b.top && a.right >= b.right && a.bottom >= b.bottom && a.width < b.width + 30; }""", sel)


def shot(page, engine, name):
    if engine == "webkit" or not WEBKIT:
        page.screenshot(path=str(HERE / "qa-shots" / name))


def run(browser, engine):
    zh = {f["id"]: f["title"]["zh"] for f in DATA["features"]}
    print(f"[{engine}] 1. a first launch: the tour")
    page = browser.new_page(viewport={"width": 1100, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub("zh-TW", "pending", "", []))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector(".sfg-card", timeout=15000)
    page.wait_for_timeout(400)
    check("it starts by itself, on the first step: open a tab", title(page).startswith(zh["new-tab"]), title(page))
    check("…no tab yet, so the + is hidden: the spotlight is on the launcher", spot_on(page, "#modal-new .modal"))
    check("…and the folded sidebar opened", not page.query_selector("#sidebar.collapsed"))
    shot(page, engine, "guide-tour-first-step.png")
    seen, settings_tab = [], None
    for _ in range(12):
        seen.append(title(page))
        if title(page).startswith(zh["telegram"]):
            settings_tab = page.evaluate("document.querySelector('#modal-settings.open .settings-tab.active')?.dataset.tab || ''")
            shot(page, engine, "guide-tour-telegram-step.png")
        if len(seen) == 2:
            check("the next step puts the launcher away", not page.query_selector("#modal-new.open"))
        btn = page.query_selector(".sfg-card .sfg-btn.primary")
        last = btn.inner_text() == "完成"
        check(f"step '{seen[-1]}': the card is inside the window", inside(page))
        btn.click()
        page.wait_for_timeout(300)
        if last:
            break
    got = [s.split("未開啟")[0] for s in seen]
    want = [zh[i] for i in DATA["tours"]["first_run"] if i != "chat-view" and (i != "usage" or zh["usage"] in got)]
    check("the steps on the page, in the tour's order (no tab yet: no chat switch)", got == want, seen)
    check("the Telegram step opened Settings on its tab", settings_tab == "bridge", settings_tab)
    check("'Done' records the tour done and the version seen", ["guide_mark", "done", True] in page.evaluate("window.__calls"),
          page.evaluate("window.__calls"))
    check("…Settings is closed again and the launcher is back (still no tab)",
          not page.query_selector("#modal-settings.open") and page.query_selector("#modal-new.open") is not None)
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 2. a computer that already ran ShellBot: no tour, the Guide button")
    page = browser.new_page(viewport={"width": 1100, "height": 720})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub("zh-TW", "skip", "0.44.5", DEMO_TAB, {"sidebar_open": True}))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector("#btn-guide .sfg-badge", timeout=15000)
    page.wait_for_timeout(800)
    check("nothing pops up", not page.query_selector(".sfg-layer"))
    n = len(DATA["features"])
    check(f"the Guide button counts what is new since 0.44.5 ({n})", page.inner_text("#btn-guide .sfg-badge") == str(n),
          page.inner_text("#btn-guide .sfg-badge"))
    page.click("#btn-guide")
    page.wait_for_selector(".sfg-help", timeout=4000)
    txt = page.inner_text(".sfg-help")
    check("the help list: what is new first, every feature", "上次看過之後的新功能" in txt and all(zh[i] in txt for i in zh), txt[:200])
    check("…a feature behind a switch that is off says so", "未開啟・在設定裡" in page.inner_text('.sfg-row[data-id="a2a"]'))
    shot(page, engine, "guide-help-list.png")
    page.wait_for_timeout(300)
    check("opening it records this version as seen; the count goes away",
          ["guide_mark", "", True] in page.evaluate("window.__calls") and not page.query_selector("#btn-guide .sfg-badge"))

    print(f"[{engine}] 3. show me: a switch that is off")
    page.click('.sfg-row[data-id="a2a"] [data-show]')
    page.wait_for_selector(".sfg-card", timeout=4000)
    page.wait_for_timeout(300)
    check("Settings opens and the spotlight is on its switch",
          page.query_selector("#modal-settings.open") is not None and spot_on(page, "#setting-experimental-a2a"))
    check("…the card says it is off and how to turn it on", "用這裡的開關打開" in page.inner_text(".sfg-card"))
    shot(page, engine, "guide-show-me-a2a.png")
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    check("Esc closes it, and the Settings it opened", not page.query_selector(".sfg-layer")
          and not page.query_selector("#modal-settings.open"))
    check("no page errors", not errors, errors)
    page.close()

    print(f"[{engine}] 4. English, a small window")
    page = browser.new_page(viewport={"width": 640, "height": 520})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub("en", "pending", "", []))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector(".sfg-card", timeout=15000)
    page.wait_for_timeout(400)
    en = {f["id"]: f["title"]["en"] for f in DATA["features"]}
    check("the words are English", title(page).startswith(en["new-tab"]) and "Next" in page.inner_text(".sfg-card"), title(page))
    check("the card is inside a 640×520 window", inside(page))
    page.click(".sfg-card .sfg-btn:not(.primary)")       # Skip
    page.wait_for_timeout(300)
    check("Skip ends it as done too", ["guide_mark", "done", True] in page.evaluate("window.__calls"))
    check("no page errors", not errors, errors)
    page.close()


WEBKIT = False
with sync_playwright() as pw:
    browsers = []
    for engine in ("chromium", "webkit"):
        try:
            browsers.append((engine, getattr(pw, engine).launch()))
        except Exception as e:                       # webkit is optional: `playwright install webkit`
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
    WEBKIT = any(e == "webkit" for e, _ in browsers)
    for engine, browser in browsers:
        try:
            run(browser, engine)
        finally:
            browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
