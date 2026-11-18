#!/usr/bin/env python3
"""The office keeps its layout when the window is narrow, at every Interface size.

Reported: shrinking the window broke the office's layout: at Interface size large (1.18) the top bar's right-hand
controls (search, the zoning and area menus, flat/iso) slid over the status counts. The breakpoints were written in
window pixels, while the bar is zoomed by --ui, so a 1027px window at 1.18 has the room of an 870px one.
For each width × Interface size this checks, in the embedded office (live data from the stand-in host):
  - the top bar's pieces (brand, the four counts, every visible control) do not overlap and stay on screen
  - the legend and the bottom-right buttons do not overlap and stay on screen
  - the page never scrolls sideways
  - the opened board and calendar sheets fit the window and their header pieces do not overlap
  - the counts keep their words (忙／等你／卡住／閒置): when they do not fit they move to a second row
  - a wide window is not squeezed, growing the window back undoes it, the reported 1027px at 1.18 stays one row,
    a count growing to more digits re-fits the bar, and opening the search box does not re-shuffle it
Demo data only. Headless Chromium and WebKit (the Mac app is WebKit). Run: .venv/bin/python tests_office_narrow.py
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


# Overlap / off-screen finder. Rects in the office frame's viewport (zoom already applied by the engine).
PROBE = r"""(what) => {
  const W = innerWidth, Hh = innerHeight, bad = [];
  const vis = el => { if (!el) return false; const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    for (let p = el; p; p = p.parentElement) if (p.hidden) return false;
    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const R = el => el.getBoundingClientRect();
  const ov = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 1;
  const name = el => el.id ? '#' + el.id : (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : el.tagName) + (el.dataset.k ? '[' + el.dataset.k + ']' : '');
  const pairs = (list, maxR = W) => { list = list.filter(vis);
    for (let i = 0; i < list.length; i++) { const a = R(list[i]);
      if (a.left < -1 || a.right > maxR + 1 || a.top < -1 || a.bottom > Hh + 1) bad.push(name(list[i]) + ' off screen ' + [a.left, a.right, a.top, a.bottom].map(Math.round));
      for (let j = i + 1; j < list.length; j++) if (ov(a, R(list[j]))) bad.push(name(list[i]) + ' × ' + name(list[j])); } };
  if (what === 'top') {
    pairs([document.querySelector('.brand b'), document.querySelector('.brand .tag'), ...document.querySelectorAll('#stats .stat'),
           ...document.getElementById('topRight').children], W - (document.body.classList.contains('embedded') ? 52 : 0));   // ShellBot's ✕ corner
    const se = document.scrollingElement; if (se.scrollWidth > W + 1) bad.push('page scrolls sideways ' + se.scrollWidth + '>' + W);
    document.querySelectorAll('#stats .lb').forEach(l => { if (!vis(l)) bad.push('a count lost its word: ' + l.textContent); });
  } else if (what === 'bottom') {
    pairs([document.getElementById('legend'), document.getElementById('tools')]);
    pairs([...document.querySelectorAll('#tools > *')]);
  } else {
    pairs([document.getElementById('wBox')]);
    const h = document.querySelector('#wall .vhead');
    pairs([h.querySelector('.vic'), h.querySelector('.vt'), ...h.querySelectorAll('.vhx > *'), h.querySelector('.vx')]);
    const t = document.getElementById('vTitle'); if (vis(t) && t.scrollWidth > t.clientWidth + 1) bad.push('title clipped');
  }
  return bad;
}"""

WIDTHS = (1440, 1240, 1100, 1027, 960, 900, 820, 760, 720, 640, 560, 480)
SCALES = (0.85, 1, 1.18)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP: playwright not installed")
    sys.exit(0)

srv = H.serve()
base = f"http://127.0.0.1:{srv.server_address[1]}"
with sync_playwright() as p:
    for engine in ("chromium", "webkit"):
        print(f"── {engine}")
        b = getattr(p, engine).launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
        page.add_init_script(H.stub())
        page.goto(base + "/index.html")
        page.wait_for_function("window.SFOffice && window.SFBots && SFBots.enabled()", timeout=20000)
        fails = {}
        for s in SCALES:
            page.evaluate("(s) => document.documentElement.style.setProperty('--ui-scale', String(s))", s)
            fr = H.open_office(page)
            for w in WIDTHS:
                page.set_viewport_size({"width": w, "height": 820})
                page.wait_for_timeout(350)
                for what in ("top", "bottom"):
                    bad = fr.evaluate(PROBE, what)
                    if bad:
                        fails.setdefault(what, []).append(f"{w}px@{s}: " + "; ".join(bad[:4]))
                for view in ("board", "cal"):
                    fr.evaluate("(v) => __office.openView(v)", view)
                    page.wait_for_timeout(300)
                    bad = fr.evaluate(PROBE, "sheet")
                    if bad:
                        fails.setdefault(view, []).append(f"{w}px@{s}: " + "; ".join(bad[:4]))
                    fr.evaluate("__office.closeView()")
                if engine == "webkit" and s == 1.18 and w in (1027, 760):
                    H.SHOTS.mkdir(exist_ok=True)
                    page.screenshot(path=str(H.SHOTS / f"office-narrow-{w}-118.png"))
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(350)
            f = fr.evaluate("__office.fit()")
            check(f"{engine}@{s}: back at 1440px nothing is squeezed and the legend sits in its corner",
                  f["top"] == 0 and not f["legendBottom"] and "one" not in f["legend"], f)
            if s == 1.18:
                page.set_viewport_size({"width": 1027, "height": 820})
                page.wait_for_timeout(350)
                f = fr.evaluate("__office.fit()")
                check(f"{engine}: the reported 1027px window at 1.18 keeps one row",
                      "f-wrap" not in f["cls"] and fr.evaluate(PROBE, "top") == [], f)
                fr.evaluate("document.querySelector('#stats .stat[data-k=idle] b').textContent = '123456'")
                page.wait_for_timeout(350)
                check(f"{engine}: a count growing to more digits re-fits the bar",
                      fr.evaluate(PROBE, "top") == [] and fr.evaluate("__office.fit().top") > f["top"],
                      (fr.evaluate(PROBE, "top"), fr.evaluate("__office.fit()")))
                page.set_viewport_size({"width": 900, "height": 820})
                page.wait_for_timeout(350)
                before = fr.evaluate("__office.fit().cls")
                fr.click("#searchBtn")
                page.wait_for_timeout(350)
                opened = fr.evaluate("() => { const r = document.getElementById('search').getBoundingClientRect();"
                                     " return document.body.classList.contains('searching') && r.width > 300 && document.activeElement.id === 'q'; }")
                during = fr.evaluate("__office.fit().cls")
                fr.press("#q", "Escape")
                page.wait_for_timeout(350)
                check(f"{engine}: the search button opens the search box over the bar, without re-shuffling it",
                      opened and during == before and fr.evaluate("!document.body.classList.contains('searching')"), (opened, before, during))
                page.set_viewport_size({"width": 1440, "height": 900})
                page.wait_for_timeout(300)
        for what, label in (("top", "the top bar's pieces never overlap or leave the screen"),
                            ("bottom", "the legend and the bottom-right buttons never overlap"),
                            ("board", "the opened board fits and its header never overlaps"),
                            ("cal", "the opened calendar fits and its header never overlaps")):
            got = fails.get(what, [])
            check(f"{engine}: {label} ({len(WIDTHS)} widths × {len(SCALES)} sizes)", not got, "\n      " + "\n      ".join(got[:12]))
        demo = []   # office.html on its own: the demo page also has ＋ 新增正職 / ＋10 and the module tag in the bar
        for q in ("", "?ui=1.18"):
            d = b.new_page(viewport={"width": 1440, "height": 820})
            d.on("pageerror", lambda e: errors.append(f"pageerror {e}"))
            d.goto(base + "/office.html" + q)
            d.wait_for_function("window.__office", timeout=15000)
            for w in WIDTHS:
                d.set_viewport_size({"width": w, "height": 820})
                d.wait_for_timeout(250)
                for what in ("top", "bottom"):
                    bad = d.evaluate(PROBE, what)
                    if bad:
                        demo.append(f"{w}px{q}: " + "; ".join(bad[:4]))
            d.close()
        check(f"{engine}: the demo page (no host) fits too, at 1 and 1.18", not demo, "\n      " + "\n      ".join(demo[:12]))
        check(f"{engine}: no page error", not errors, errors[:3])
        b.close()
srv.shutdown()
print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
