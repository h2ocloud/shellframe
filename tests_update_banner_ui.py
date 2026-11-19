#!/usr/bin/env python3
"""The tab bar's update notice (「vX 可更新（點我）」) stays on one line when the tab bar is crowded.

Reported with a screenshot: with many tabs open, the notice was squeezed into a narrow column, its words stacked a
character or two per line and spilling below the bar. It is a flex item between the tabs and the right-hand tools;
it now keeps its own width (white-space: nowrap; flex-shrink: 0) and the tabs give way.
web/index.html with a stand-in Python side (tests_settings_ui.open_page), the notice placed where the app places it
(after the tab bar's spacer), the bar crowded with copies of a tab. Chromium and WebKit. Demo data only.
Run: .venv/bin/python tests_update_banner_ui.py
"""
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("SKIP  tests_update_banner_ui.py（沒裝 playwright）")
    sys.exit(0)
import tests_settings_ui as S  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


PLACE = """(n) => {
  const tab = document.querySelector('#tab-bar .tab');
  if (tab) for (let i = 0; i < n; i++) { const c = tab.cloneNode(true); c.removeAttribute('id'); c.dataset.sid = 'demo' + i; tab.parentNode.appendChild(c); }
  const spacer = document.querySelector('#tab-bar [class*=spacer]');
  const b = document.createElement('span'); b.className = 'update-banner'; b.id = 'update-banner-btn';
  b.textContent = 'v0.59.1 可更新（點我）';
  spacer.parentNode.insertBefore(b, spacer.nextSibling);
  const r = b.getBoundingClientRect(), bar = document.getElementById('tab-bar').getBoundingClientRect();
  const lh = parseFloat(getComputedStyle(b).lineHeight) || 18;
  return {h: r.height, w: r.width, sw: b.scrollWidth, lines: Math.round((r.height - 4) / lh), barBottom: bar.bottom, bottom: r.bottom};
}"""

with sync_playwright() as pw:
    for engine in ("chromium", "webkit"):
        try:
            browser = getattr(pw, engine).launch()
        except Exception as e:
            print(f"SKIP  {engine}: {str(e).splitlines()[0][:80]}")
            continue
        try:
            print(f"[{engine}]")
            for width, extra in ((1280, 14), (900, 10)):
                page, errors = S.open_page(browser, "zh-TW", {})
                page.set_viewport_size({"width": width, "height": 700})
                page.wait_for_timeout(1200)
                r = page.evaluate(PLACE, extra)
                check(f"{width} px, a crowded tab bar: the notice is one line, its full text, inside the bar",
                      r["lines"] <= 1 and r["sw"] <= r["w"] + 1 and r["bottom"] <= r["barBottom"] + 1, r)
                check(f"{width} px: no page errors", not errors, errors)
                page.close()
        finally:
            browser.close()

print(f"\nResults: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
