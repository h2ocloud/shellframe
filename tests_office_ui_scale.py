#!/usr/bin/env python3
"""The office follows Settings' Interface size (small / medium / large).

Asked for: the office's words (the legend in the bottom-left corner, the list in the opened whiteboard) were too
small, and should grow and shrink with the Interface size setting. The office is an iframe; it ignored the main
page's --ui-scale. Now SFOffice.host.uiScale() sets the office's --ui, and its DOM chrome is zoomed by it:
  - --ui follows --ui-scale (0.85 / 1 / 1.18), also when the setting changes while the office is open
  - the legend's rows, the top bar and the opened sheet's title grow with it
  - the canvas is not zoomed: a click on the wall's whiteboard still opens it at 1.18
  - office.html on its own (the demo page) reads ?ui= and clamps it
Demo data only. Headless Chromium. Run: .venv/bin/python tests_office_ui_scale.py
"""
import pathlib
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
_src = (HERE / "tests_office_board_ui.py").read_text(encoding="utf-8")
H = types.ModuleType("office_board_harness")
H.__file__ = str(HERE / "tests_office_board_ui.py")
exec(compile(_src[:_src.index("\ndef run(")], H.__file__, "exec"), H.__dict__)   # its stand-in Python side and helpers, not its run

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def measure(fr):
    return fr.evaluate("""() => ({ui: getComputedStyle(document.documentElement).getPropertyValue('--ui').trim(),
        li: document.querySelector('#legend li').getBoundingClientRect().height,
        top: document.getElementById('top').getBoundingClientRect().height})""")


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

srv = H.serve()
base = f"http://127.0.0.1:{srv.server_address[1]}"
with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
    page.add_init_script(H.stub())
    page.goto(base + "/index.html")
    page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
    got = {}
    for s in (0.85, 1, 1.18):
        page.evaluate("(s) => document.documentElement.style.setProperty('--ui-scale', String(s))", s)
        fr = H.open_office(page)
        got[s] = measure(fr)
        if s == 1.18:
            H.SHOTS.mkdir(exist_ok=True)
            page.screenshot(path=str(H.SHOTS / "office-ui-scale-118.png"))
            pt, tip, cursor = H.wall_click(page, fr, "board")
            check("at 1.18 a click on the wall's whiteboard still opens it (the canvas is not zoomed)",
                  tip == "任務看板" and fr.evaluate("__office.view()") == "board" and fr.evaluate("!document.getElementById('wall').hidden"),
                  (tip, fr.evaluate("__office.view()")))
            t118 = fr.evaluate("document.getElementById('vTitle').getBoundingClientRect().height")
            fr.evaluate("__office.closeView()")
    check("--ui follows the main page's --ui-scale", [got[s]["ui"] for s in (0.85, 1, 1.18)] == ["0.85", "1", "1.18"], got)
    check("the legend's rows grow with it", got[0.85]["li"] < got[1]["li"] < got[1.18]["li"]
          and abs(got[1.18]["li"] / got[1]["li"] - 1.18) < .04, got)
    check("…and the top bar", got[0.85]["top"] < got[1]["top"] < got[1.18]["top"], got)

    print("a change while the office is open")
    page.evaluate("document.documentElement.style.setProperty('--ui-scale', '1')")
    page.evaluate("SFOffice.refresh()")
    page.wait_for_timeout(600)
    fr = H.office(page)
    check("the next snapshot takes the new size", measure(fr)["ui"] == "1", measure(fr))
    fr.evaluate("__office.openView('board')")
    page.wait_for_timeout(400)
    t1 = fr.evaluate("document.getElementById('vTitle').getBoundingClientRect().height")
    check("the opened sheet's title is larger at 1.18 than at 1", t118 > t1 * 1.1, (t118, t1))
    page.evaluate("SFOffice.close()")

    print("the office on its own (demo page)")
    for q, want in (("?ui=1.18", "1.18"), ("?ui=9", "1.6"), ("", "1")):
        d = b.new_page(viewport={"width": 1280, "height": 800})
        d.goto(base + "/office.html" + q)
        d.wait_for_function("window.__office", timeout=15000)
        check(f"office.html{q or ' (no ?ui)'} → --ui {want}", d.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ui').trim()") == want)
        d.close()
    check("no page error", not errors, errors[:3])
    b.close()
srv.shutdown()
print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
