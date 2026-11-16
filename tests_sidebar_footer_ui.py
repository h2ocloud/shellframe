#!/usr/bin/env python3
"""The sidebar's foot is one row (web/index.html #sb-footer-row), in the real page with a stand-in Python side.

Reported: four stacked rows at the bottom left (Settings, About, Guide, For the AI) read as clutter.
  - one row: Settings keeps its word; the guide, the AI's manual and About are icons
  - each icon says its name (title and aria-label, in the page's language)
  - the guide's count of new features sits on its icon
  - every button does what it did: Settings, the guide's list, About, About at the AI's manual
  - the chat view's dock (usage, Telegram, loops) stays above the row; nothing spills out of a narrow sidebar
Run in Chromium, and in WebKit when playwright has it. Demo data only. Screenshots: qa-shots/sidebar-footer-*.png.
Needs playwright; SKIP without it. Run: .venv/bin/python tests_sidebar_footer_ui.py
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
    print("SKIP  tests_sidebar_footer_ui.py（沒裝 playwright）")
    sys.exit(0)

DATA = guide.load()


def stub(style):
    settings = {"language": "zh-TW", "ui_style": style, "experimental_bots": True, "sidebar_open": True}
    cfg = {"settings": settings, "presets": [{"name": "Claude", "cmd": "claude", "icon": "C"}],
           "agent_roster": {}, "guide": {"tour": "done", "seen": "0.44.5"}}
    gdata = {"success": True, "features": guide.for_surface("desktop", settings, DATA), "tours": DATA.get("tours"),
             "state": {"tour": "done", "seen": "0.44.5", "version": guide.app_version()}}
    sessions = [{"sid": "s601", "cmd": "claude", "alive": True, "label": "示範分頁"},
                {"sid": "s602", "cmd": "claude", "alive": True, "label": "示範助理"}]
    return """
window.__cfg = %s; window.__sessions = %s; window.__guide = %s;
const R = v => Promise.resolve(typeof v === 'string' ? v : JSON.stringify(v));
window.pywebview = { api: new Proxy({}, { get: (t, prop) => {
  if (prop === 'then') return undefined;
  return (...args) => {
    switch (prop) {
      case 'get_config': return R(window.__cfg);
      case 'list_sessions': return R(window.__sessions);
      case 'get_active_tab': return R({sid: 's601'});
      case 'read_output': case 'plugin_sidebar_badges': return R('');
      case 'guide_data': return R(window.__guide);
      case 'guide_mark': return R({success: true, state: window.__guide.state});
      case 'get_changelog': return R('');
      case 'preset_groups': return R([]);
      default: return R('{}');
    }
  };
}})};
""" % tuple(json.dumps(x, ensure_ascii=False) for x in (cfg, sessions, gdata))


ROW = "#sb-footer-row"
ICONS = {"#btn-guide": "導覽", "#btn-ai-skill": "給 AI 的說明", "#btn-about": "關於"}


def boxes(page):
    return page.evaluate("""() => ['#btn-settings', '#btn-guide', '#btn-ai-skill', '#btn-about'].map(s => {
      const r = document.querySelector(s).getBoundingClientRect(); return {s, top: r.top, bottom: r.bottom, left: r.left, right: r.right}; })""")


def run(browser, engine, style):
    page = browser.new_page(viewport={"width": 1000, "height": 640})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(stub(style))
    page.goto((HERE / "web" / "index.html").as_uri())
    page.wait_for_selector(ROW, timeout=15000)
    page.wait_for_selector("#btn-guide .sfg-badge", timeout=15000)
    page.wait_for_timeout(300)
    shoot = engine == "chromium"
    print(f"[{engine} · {style}] one row")
    b = boxes(page)
    check("the four buttons share one row", max(x["top"] for x in b) - min(x["top"] for x in b) <= 2, b)
    check("…left to right: Settings, then the three icons", [x["s"] for x in sorted(b, key=lambda x: x["left"])]
          == ["#btn-settings", "#btn-guide", "#btn-ai-skill", "#btn-about"])
    check("Settings keeps its word", page.inner_text("#btn-settings").strip().endswith("設定"), page.inner_text("#btn-settings"))
    for sel, name in ICONS.items():
        got = page.evaluate("(s) => { const e = document.querySelector(s); return [e.title, e.getAttribute('aria-label')]; }", sel)
        check(f"{sel} says its name: {name}", got == [name, name], got)
    check("the icons show no words in the row", all(page.evaluate(
        "(s) => document.querySelector(s + ' .sb-ico-label').getBoundingClientRect().width <= 1", s) for s in ICONS))
    badge = page.locator("#btn-guide .sfg-badge").bounding_box()
    guide_box = page.locator("#btn-guide").bounding_box()
    ai_box = page.locator("#btn-ai-skill").bounding_box()
    check("the guide's count of new features sits on its icon's top-right corner, clear of the next icon",
          badge and guide_box["x"] < badge["x"] < guide_box["x"] + guide_box["width"]
          and badge["y"] < guide_box["y"] + guide_box["height"] / 2
          and badge["x"] + badge["width"] <= ai_box["x"] + ai_box["width"] / 2, (badge, guide_box, ai_box))
    if style == "chat":
        r = page.evaluate("""() => { const d = document.getElementById('sb-dock'), row = document.getElementById('sb-footer-row');
          return d && d.children.length ? [d.getBoundingClientRect().bottom, row.getBoundingClientRect().top] : null; }""")
        check("the chat view's dock stays above the row", r is None or r[0] <= r[1] + 1, r)
    if shoot:
        page.locator("#sidebar").screenshot(path=str(HERE / "qa-shots" / f"sidebar-footer-{style}.png"))

    print(f"[{engine} · {style}] a narrow sidebar")
    page.evaluate("document.getElementById('sidebar').style.width = '170px'")
    page.wait_for_timeout(150)
    over = page.evaluate("""() => { const f = document.getElementById('sidebar-footer').getBoundingClientRect();
      return ['#btn-settings', '#btn-guide', '#btn-ai-skill', '#btn-about'].filter(s => {
        const r = document.querySelector(s).getBoundingClientRect(); return r.right > f.right + 0.5 || r.left < f.left - 0.5; }); }""")
    check("nothing spills out of a 170 px sidebar", not over, over)
    check("…still one row", max(x["top"] for x in boxes(page)) - min(x["top"] for x in boxes(page)) <= 2)
    if shoot:
        page.locator("#sidebar").screenshot(path=str(HERE / "qa-shots" / f"sidebar-footer-{style}-narrow.png"))
    page.evaluate("document.getElementById('sidebar').style.width = ''")

    print(f"[{engine} · {style}] each does what it did")
    page.click("#btn-guide")
    page.wait_for_selector(".sfg-help-veil", timeout=5000)
    check("the guide icon opens the guide's list", bool(page.query_selector(".sfg-help")))
    page.click(".sfg-help [data-act='close']")
    page.click("#btn-about")
    page.wait_for_timeout(300)
    check("the ? opens About", page.evaluate("document.getElementById('modal-about').classList.contains('open')"))
    page.evaluate("document.getElementById('modal-about').classList.remove('open')")
    page.click("#btn-ai-skill")
    page.wait_for_timeout(500)
    check("the AI icon opens About at the AI's manual", page.evaluate("""() => {
      const m = document.getElementById('modal-about'), s = document.getElementById('about-skill-section');
      if (!m.classList.contains('open') || !s) return false;
      const r = s.getBoundingClientRect(); return r.top >= 0 && r.top < innerHeight; }"""))
    page.evaluate("document.getElementById('modal-about').classList.remove('open')")
    page.click("#btn-settings")
    page.wait_for_timeout(300)
    check("Settings opens Settings", page.evaluate("document.getElementById('modal-settings').classList.contains('open')"))
    check("no page errors", not errors, errors)
    page.close()


with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            for style in ("chat", "terminal"):
                run(browser, engine, style)
        finally:
            browser.close()

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
